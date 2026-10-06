"""Quản lý tri thức cho leader (trang /quan-ly): phân quyền theo phòng ban, lịch sử sửa, liên kết [[...]].

Quy tắc quyền:
- Admin (email trong ADMIN_EMAILS) sửa được mọi note, kể cả phòng "Unassigned".
- Leader (bảng knowledge_leaders, active) chỉ thêm/sửa/xoá note thuộc phòng mình phụ trách; vẫn đọc được
  mọi note (giống chatbot — nội dung nội bộ công ty). Không đổi được note sang phòng mình không phụ trách.
- Người khác: không vào được trang quản lý.
Mọi thay đổi ghi vào knowledge_note_versions (kèm ai sửa) để khôi phục; có hiệu lực ngay với chatbot
vì AdminService tự cắt chunk + embed lại ngay khi ghi.
"""
from __future__ import annotations

import re
import unicodedata
from datetime import datetime, timezone
from typing import Any

from app.services.admin_service import AdminService, NoteNotFoundError
from app.services.supabase_client import SupabaseClient

DEPARTMENTS = ("HR", "Finance", "Academic", "Admin", "Unassigned")
SUMMARY_COLUMNS = "id,title,department,status,access_level,version,updated_at,created_by,source_file"
WIKILINK = re.compile(r"\[\[([^\]\|#\n]+)(?:#[^\]\|\n]*)?(?:\|[^\]\n]*)?\]\]")
MAX_CONTENT_CHARS = 100_000


class ManageError(Exception):
    def __init__(self, status_code: int, detail: str):
        self.status_code = status_code
        self.detail = detail
        super().__init__(detail)


def slugify(text: str) -> str:
    """Giống quy ước đặt tên file wiki: bỏ dấu, chữ thường, nối bằng '-'."""
    text = unicodedata.normalize("NFKD", text.replace("đ", "d").replace("Đ", "D"))
    text = "".join(ch for ch in text if not unicodedata.combining(ch)).lower()
    return re.sub(r"[^a-z0-9]+", "-", text).strip("-")


def extract_links(content: str) -> list[str]:
    seen: dict[str, None] = {}
    for match in WIKILINK.finditer(content or ""):
        seen[match.group(1).strip()] = None
    return list(seen)


def _source_stem(note: dict[str, Any]) -> str | None:
    source = str(note.get("source_file") or "")
    if source.startswith("vault:"):
        return source.rsplit("/", 1)[-1].removesuffix(".md").lower()
    return None


class LinkIndex:
    """Tra cứu note theo cái tên mà [[wiki-link]] dùng: tên file wiki (slug) hoặc tiêu đề."""

    def __init__(self, notes: list[dict[str, Any]]):
        self.by_key: dict[str, dict[str, Any]] = {}
        for note in notes:
            keys = {slugify(note["title"])}
            stem = _source_stem(note)
            if stem:
                keys.add(stem)
            title = str(note["title"])
            if title.startswith("Nguồn:"):  # trang tóm tắt nguồn: file wiki là "<slug>-nguon"
                keys.add(slugify(title.removeprefix("Nguồn:")) + "-nguon")
            for key in keys:
                self.by_key.setdefault(key, note)

    def resolve(self, target: str) -> dict[str, Any] | None:
        return self.by_key.get(slugify(target)) or self.by_key.get(target.strip().lower())


class ManageService:
    def __init__(self, admin: AdminService, client: SupabaseClient, admin_emails: str | None):
        self.admin = admin
        self.client = client
        self.admin_emails = {e.strip().casefold() for e in (admin_emails or "").split(",") if e.strip()}

    # ---- leader & quyền ----
    def list_leaders(self) -> list[dict[str, Any]]:
        return self.client.select("knowledge_leaders", {"select": "*", "order": "email.asc"})

    def upsert_leader(self, email: str, display_name: str, departments: list[str], active: bool = True) -> dict[str, Any]:
        email = email.strip().casefold()
        unknown = [d for d in departments if d not in DEPARTMENTS]
        if "@" not in email or unknown:
            raise ManageError(422, "Email hoặc phòng ban không hợp lệ.")
        row = {"email": email, "display_name": display_name.strip(), "departments": sorted(set(departments)),
               "active": active, "updated_at": datetime.now(timezone.utc).isoformat()}
        existing = self.client.select("knowledge_leaders", {"select": "email", "email": f"eq.{email}"})
        if existing:
            return self.client.update("knowledge_leaders", {"email": f"eq.{email}"}, row)[0]
        return self.client.insert("knowledge_leaders", [row])[0]

    def delete_leader(self, email: str) -> None:
        self.client.delete("knowledge_leaders", {"email": f"eq.{email.strip().casefold()}"})

    def profile(self, email: str | None) -> dict[str, Any]:
        normalized = (email or "").strip().casefold()
        is_admin = normalized in self.admin_emails
        departments: list[str] = []
        if normalized and not is_admin:
            rows = self.client.select("knowledge_leaders", {
                "select": "departments", "email": f"eq.{normalized}", "active": "eq.true", "limit": "1"})
            departments = [d for d in (rows[0]["departments"] if rows else []) if d in DEPARTMENTS]
        elif is_admin:
            departments = list(DEPARTMENTS)
        return {"email": normalized, "is_admin": is_admin, "departments": departments,
                "can_manage": bool(departments)}

    def require_manager(self, profile: dict[str, Any]) -> None:
        if not profile["can_manage"]:
            raise ManageError(403, "Tài khoản này chưa được cấp quyền quản lý tri thức. Liên hệ admin.")

    @staticmethod
    def can_edit(profile: dict[str, Any], department: str) -> bool:
        return department in profile["departments"]

    def _require_edit(self, profile: dict[str, Any], department: str) -> None:
        self.require_manager(profile)
        if not self.can_edit(profile, department):
            raise ManageError(403, f"Bạn không phụ trách phòng {department}.")

    # ---- đọc ----
    def list_notes(self, profile: dict[str, Any], q: str = "", department: str = "") -> list[dict[str, Any]]:
        self.require_manager(profile)
        params = {"select": SUMMARY_COLUMNS, "order": "department.asc,title.asc", "limit": "1000"}
        if department:
            params["department"] = f"eq.{department}"
        term = re.sub(r"[(),*%\\]", " ", q).strip()
        if term:
            params["or"] = f"(title.ilike.*{term}*,content.ilike.*{term}*)"
        notes = self.client.select("knowledge_notes", params)
        for note in notes:
            note["editable"] = self.can_edit(profile, note["department"])
        return notes

    def get_note(self, profile: dict[str, Any], note_id: str) -> dict[str, Any]:
        self.require_manager(profile)
        note = self.admin.get_note(note_id)
        note["editable"] = self.can_edit(profile, note["department"])
        summaries = self.client.select("knowledge_notes", {"select": SUMMARY_COLUMNS + ",content", "limit": "1000"})
        index = LinkIndex(summaries)
        outgoing, unresolved = [], []
        for target in extract_links(note["content"]):
            hit = index.resolve(target)
            if hit and hit["id"] != note["id"]:
                outgoing.append({"id": hit["id"], "title": hit["title"], "department": hit["department"], "target": target})
            elif not hit:
                unresolved.append(target)
        incoming = []
        for other in summaries:
            if other["id"] == note["id"]:
                continue
            for target in extract_links(other.get("content") or ""):
                resolved = index.resolve(target)
                if resolved and resolved["id"] == note["id"]:
                    incoming.append({"id": other["id"], "title": other["title"], "department": other["department"]})
                    break
        note["links"] = {"outgoing": outgoing, "unresolved": unresolved, "incoming": incoming}
        return note

    # ---- lịch sử ----
    def _record(self, note: dict[str, Any], kind: str, by: str) -> None:
        rows = self.client.select("knowledge_note_versions", {
            "select": "version_no", "note_id": f"eq.{note['id']}", "order": "version_no.desc", "limit": "1"})
        number = (rows[0]["version_no"] + 1) if rows else 1
        self.client.insert("knowledge_note_versions", [{
            "note_id": str(note["id"]), "version_no": number, "change_kind": kind, "changed_by": by,
            "title": note["title"], "department": note["department"], "access_level": note.get("access_level") or "staff",
            "status": note.get("status") or "draft", "content": note["content"],
        }], returning=False)

    def versions(self, profile: dict[str, Any], note_id: str) -> list[dict[str, Any]]:
        self.require_manager(profile)
        return self.client.select("knowledge_note_versions", {
            "select": "id,version_no,change_kind,changed_by,title,department,status,created_at",
            "note_id": f"eq.{note_id}", "order": "version_no.desc", "limit": "100"})

    # ---- ghi ----
    @staticmethod
    def _validate_body(title: str, content: str) -> None:
        if not title.strip() or not content.strip():
            raise ManageError(422, "Cần nhập tiêu đề và nội dung.")
        if len(content) > MAX_CONTENT_CHARS:
            raise ManageError(422, f"Nội dung dài quá {MAX_CONTENT_CHARS} ký tự.")

    def create_note(self, profile: dict[str, Any], title: str, department: str, content: str,
                    status: str = "approved", access_level: str = "staff") -> dict[str, Any]:
        self._require_edit(profile, department)
        self._validate_body(title, content)
        note = self.admin.create_note(title.strip(), department, content, access_level, status, created_by=profile["email"])
        self._record(note, "create", profile["email"])
        return note

    def update_note(self, profile: dict[str, Any], note_id: str, fields: dict[str, Any]) -> dict[str, Any]:
        current = self.admin.get_note(note_id)
        self._require_edit(profile, current["department"])
        base = fields.get("base_updated_at")
        if base and str(current.get("updated_at") or "") != base:
            raise ManageError(409, "Tài liệu vừa được người khác (hoặc đồng bộ Obsidian) cập nhật. Tải lại trang để xem bản mới rồi sửa lại, tránh ghi đè.")
        patch = {k: fields[k] for k in ("title", "department", "content", "status", "access_level") if k in fields}
        merged = {**current, **patch}
        self._validate_body(merged["title"], merged["content"])
        if merged["department"] != current["department"]:
            self._require_edit(profile, merged["department"])
        patch["reviewed_by"] = profile["email"]
        patch["reviewed_at"] = datetime.now(timezone.utc).isoformat()
        note = self.admin.save_note(note_id, patch)
        self._record(note, "update", profile["email"])
        return note

    def delete_note(self, profile: dict[str, Any], note_id: str) -> None:
        current = self.admin.get_note(note_id)
        self._require_edit(profile, current["department"])
        self._record(current, "delete", profile["email"])  # lưu bản cuối TRƯỚC khi xoá để còn khôi phục
        self.admin.delete_note(note_id)

    def restore(self, profile: dict[str, Any], note_id: str, version_id: str) -> dict[str, Any]:
        rows = self.client.select("knowledge_note_versions", {
            "select": "*", "id": f"eq.{version_id}", "note_id": f"eq.{note_id}", "limit": "1"})
        if not rows:
            raise ManageError(404, "Không tìm thấy phiên bản này.")
        version = rows[0]
        self._require_edit(profile, version["department"])
        fields = {k: version[k] for k in ("title", "department", "content", "status", "access_level")}
        try:
            self._require_edit(profile, self.admin.get_note(note_id)["department"])
            note = self.admin.save_note(note_id, {**fields, "reviewed_by": profile["email"]})
        except NoteNotFoundError:  # note đã bị xoá: tạo lại từ bản lưu
            note = self.admin.create_note(fields["title"], fields["department"], fields["content"],
                                          fields["access_level"], fields["status"], created_by=profile["email"])
            self.client.update("knowledge_note_versions", {"note_id": f"eq.{note_id}"}, {"note_id": str(note["id"])})
        self._record(note, "restore", profile["email"])
        return note
