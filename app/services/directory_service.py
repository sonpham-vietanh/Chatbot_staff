"""Danh bạ nhân sự do HR quản lý: tạo/sửa/ngừng/kích hoạt lại, nhập hàng loạt và nhật ký thay đổi.

Chỉ admin (X-Admin-Token) gọi các thao tác này; nhân viên chỉ gửi yêu cầu chỉnh sửa (change_request_service).
Danh bạ chỉ vài trăm người nên tìm kiếm lấy tối đa vài nghìn dòng rồi lọc ở Python (không phân biệt hoa thường/dấu)."""
from __future__ import annotations

import logging
import re
from datetime import date, datetime, timedelta, timezone
from typing import Any

from app.rag.lexical_store import fold
from app.services.supabase_client import SupabaseClient

logger = logging.getLogger(__name__)

# Trường nhân viên được gửi yêu cầu sửa: tên hiển thị + độ dài tối đa + kiểu nhập.
REQUESTABLE_FIELDS: dict[str, dict[str, Any]] = {
    "display_name": {"label": "Họ tên", "max_length": 100, "type": "text"},
    "job_title": {"label": "Chức danh", "max_length": 100, "type": "text"},
    "department": {"label": "Phòng ban", "max_length": 100, "type": "text"},
    "campus": {"label": "Cơ sở", "max_length": 100, "type": "text"},
    "work_phone": {"label": "Điện thoại công việc", "max_length": 30, "type": "text"},
    "employment_start_date": {"label": "Ngày vào công ty", "max_length": 10, "type": "date"},
    "team_name": {"label": "Tổ chuyên môn", "max_length": 100, "type": "text"},
    "extra_roles": {"label": "Kiêm nhiệm", "max_length": 200, "type": "text"},
    "subjects": {"label": "Môn dạy", "max_length": 200, "type": "text"},
    "grade_levels": {"label": "Khối lớp phụ trách", "max_length": 100, "type": "text"},
}
EMPLOYMENT_TYPES = {"co_huu": "Cơ hữu", "thinh_giang": "Thỉnh giảng", "hop_dong": "Hợp đồng", "khac": "Khác"}

# Toàn bộ cột HR quản lý (ngoài email); dùng để kiểm tra và giới hạn độ dài khi HR sửa trực tiếp.
TEXT_LIMITS = {
    "display_name": 100, "department": 100, "job_title": 100, "campus": 100, "work_phone": 30, "employee_code": 30,
    "level_name": 60, "contract_type": 60, "team_name": 100, "extra_roles": 200, "subjects": 200, "grade_levels": 100,
}
DATE_FIELDS = ("employment_start_date", "employment_end_date")
REQUIRED_FIELDS = ("display_name", "department", "job_title")
EDITABLE_FIELDS = (*TEXT_LIMITS, *DATE_FIELDS, "manager_email", "employment_type")
IMPORT_COLUMNS = ("email", "display_name", "department", "job_title", "employment_start_date", "manager_email", "campus", "work_phone",
                  "employee_code", "level_name", "contract_type", "employment_type", "team_name", "extra_roles", "subjects", "grade_levels")
DIRECTORY_COLUMNS = "email,display_name,department,job_title,employment_start_date,employment_end_date,active,manager_email,campus,work_phone," \
                    "employee_code,level_name,contract_type,employment_type,team_name,extra_roles,subjects,grade_levels,created_at,updated_at"
MAX_IMPORT_ROWS = 1000
MAX_SCAN_ROWS = 5000
EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")
_TYPE_LOOKUP = {**{code: code for code in EMPLOYMENT_TYPES}, **{fold(label): code for code, label in EMPLOYMENT_TYPES.items()}}
VN_TZ = timezone(timedelta(hours=7))


class DirectoryError(Exception):
    def __init__(self, status_code: int, detail: str):
        super().__init__(detail)
        self.status_code = status_code
        self.detail = detail


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _text(value: Any) -> str:
    return "" if value is None else str(value)


def normalize_email(value: Any) -> str:
    return "".join(_text(value).split()).casefold()


def parse_date(value: Any, label: str) -> str:
    raw = _text(value).strip()
    try:
        if len(raw) != 10:
            raise ValueError(raw)
        return date.fromisoformat(raw).isoformat()
    except ValueError as error:
        raise DirectoryError(422, f"{label} phải có dạng YYYY-MM-DD.") from error


def field_label(field: str) -> str:
    if field in REQUESTABLE_FIELDS:
        return REQUESTABLE_FIELDS[field]["label"]
    return {"employee_code": "Mã nhân viên", "level_name": "Bậc/cấp", "contract_type": "Loại hợp đồng", "manager_email": "Quản lý trực tiếp",
            "employment_type": "Hình thức", "employment_end_date": "Ngày nghỉ việc"}.get(field, field)


def parse_employment_type(value: Any) -> str:
    key = fold(_text(value).strip())
    code = _TYPE_LOOKUP.get(key)
    if code is None:
        raise DirectoryError(422, "Hình thức không hợp lệ (chọn: Cơ hữu, Thỉnh giảng, Hợp đồng, Khác).")
    return code


def clean_fields(payload: dict[str, Any], *, allow_clear: bool, ignore_blank: bool = False) -> dict[str, Any]:
    """Chuẩn hoá và kiểm tra các khoá HR được phép. `allow_clear`: chuỗi rỗng/None = xoá giá trị (trừ trường bắt buộc)."""
    out: dict[str, Any] = {}
    for key, raw in payload.items():
        if key not in EDITABLE_FIELDS:
            continue
        value = " ".join(_text(raw).split())
        if not value:
            if ignore_blank:
                continue
            if key in REQUIRED_FIELDS:
                raise DirectoryError(422, f"{field_label(key)} không được để trống.")
            if allow_clear:
                out[key] = None
            continue
        if key in TEXT_LIMITS:
            if len(value) > TEXT_LIMITS[key]:
                raise DirectoryError(422, f"{field_label(key)} dài tối đa {TEXT_LIMITS[key]} ký tự.")
            out[key] = value
        elif key in DATE_FIELDS:
            out[key] = parse_date(value, field_label(key))
        elif key == "manager_email":
            manager = normalize_email(value)
            if not EMAIL_RE.match(manager):
                raise DirectoryError(422, "Email quản lý trực tiếp không hợp lệ.")
            out[key] = manager
        elif key == "employment_type":
            out[key] = parse_employment_type(value)
    return out


def diff(old: dict[str, Any] | None, new: dict[str, Any]) -> dict[str, dict[str, Any]]:
    old = old or {}
    return {key: {"old": old.get(key), "new": value} for key, value in new.items() if _text(old.get(key)) != _text(value)}


class DirectoryService:
    def __init__(self, client: SupabaseClient, allowed_email_domains: str | None = ""):
        self.client = client
        self.allowed_domains = [d.strip().casefold().lstrip("@") for d in (allowed_email_domains or "").split(",") if d.strip()]

    # ---------- đọc ----------
    def get(self, email: str) -> dict[str, Any] | None:
        rows = self.client.select("employee_directory", {"select": DIRECTORY_COLUMNS, "email": f"eq.{normalize_email(email)}", "limit": "1"})
        return rows[0] if rows else None

    def _require(self, email: str) -> dict[str, Any]:
        row = self.get(email)
        if row is None:
            raise DirectoryError(404, f"Không tìm thấy nhân viên: {normalize_email(email)}")
        return row

    def list(self, q: str = "", active: str = "true", limit: int = 50, offset: int = 0) -> dict[str, Any]:
        params = {"select": DIRECTORY_COLUMNS, "order": "display_name.asc", "limit": str(MAX_SCAN_ROWS)}
        if active in ("true", "false"):
            params["active"] = f"eq.{active}"
        rows = self.client.select("employee_directory", params)
        needle = fold(q.strip())
        if needle:
            rows = [r for r in rows if any(needle in fold(_text(r.get(k))) for k in ("email", "display_name", "department", "job_title", "employee_code"))]
        limit = max(1, min(int(limit), 200))
        offset = max(0, int(offset))
        return {"items": rows[offset: offset + limit], "total": len(rows)}

    def history(self, email: str) -> list[dict[str, Any]]:
        return self.client.select("profile_audit_log", {
            "select": "id,email,actor,action,changes,created_at", "email": f"eq.{normalize_email(email)}", "order": "created_at.desc", "limit": "100"})

    # ---------- nhật ký ----------
    def audit(self, email: str, actor: str, action: str, changes: dict[str, Any]) -> None:
        """Ghi nhật ký; lỗi ghi nhật ký không được làm hỏng thao tác chính."""
        try:
            self.client.insert("profile_audit_log", [{"email": email, "actor": actor, "action": action, "changes": changes}], returning=False)
        except Exception:
            logger.warning("Không ghi được profile_audit_log", exc_info=True)

    # ---------- kiểm tra ----------
    def _check_email(self, raw: Any, label: str = "Email") -> str:
        email = normalize_email(raw)
        if not EMAIL_RE.match(email):
            raise DirectoryError(422, f"{label} không hợp lệ.")
        if self.allowed_domains and email.rsplit("@", 1)[-1] not in self.allowed_domains:
            raise DirectoryError(422, f"{label} phải thuộc miền công ty ({', '.join('@' + d for d in self.allowed_domains)}).")
        return email

    @staticmethod
    def _creates_cycle(email: str, manager: str | None, managers: dict[str, str | None]) -> bool:
        current, seen = manager, set()
        while current:
            if current == email:
                return True
            if current in seen or len(seen) > 200:
                return False  # vòng cũ không liên quan tới người này, không chặn
            seen.add(current)
            current = managers.get(current)
        return False

    def _managers(self) -> dict[str, str | None]:
        rows = self.client.select("employee_directory", {"select": "email,manager_email", "limit": str(MAX_SCAN_ROWS)})
        return {r["email"]: r.get("manager_email") for r in rows}

    def _check_manager(self, email: str, manager: str | None, managers: dict[str, str | None]) -> None:
        if not manager:
            return
        if manager == email:
            raise DirectoryError(422, "Quản lý trực tiếp không được là chính người đó.")
        if self._creates_cycle(email, manager, managers):
            raise DirectoryError(422, "Quản lý trực tiếp tạo vòng lặp trong sơ đồ quản lý.")

    # ---------- ghi ----------
    def create(self, payload: dict[str, Any]) -> dict[str, Any]:
        email = self._check_email(payload.get("email"))
        fields = clean_fields(payload, allow_clear=False)
        for key in REQUIRED_FIELDS:
            if not fields.get(key):
                raise DirectoryError(422, f"{field_label(key)} không được để trống.")
        if self.get(email):
            raise DirectoryError(409, f"Email {email} đã có trong danh bạ.")
        self._check_manager(email, fields.get("manager_email"), self._managers() if fields.get("manager_email") else {})
        rows = self.client.insert("employee_directory", [{"email": email, **fields, "active": True}])
        row = (rows or [{"email": email, **fields, "active": True}])[0]
        self.audit(email, "admin", "create", {k: {"old": None, "new": v} for k, v in fields.items()})
        return row

    def update(self, email: str, payload: dict[str, Any]) -> dict[str, Any]:
        email = normalize_email(email)
        current = self._require(email)
        fields = clean_fields(payload, allow_clear=True)
        changes = diff(current, fields)
        if not changes:
            return current
        if "manager_email" in changes:
            self._check_manager(email, changes["manager_email"]["new"], self._managers())
        patch = {key: change["new"] for key, change in changes.items()}
        rows = self.client.update("employee_directory", {"email": f"eq.{email}"}, {**patch, "updated_at": _now()})
        self.audit(email, "admin", "hr_update", changes)
        return rows[0] if rows else {**current, **patch}

    def deactivate(self, email: str, end_date: str | None = None) -> dict[str, Any]:
        email = normalize_email(email)
        current = self._require(email)
        if not current.get("active"):
            raise DirectoryError(409, "Nhân viên này đã ngừng hoạt động.")
        end = parse_date(end_date, "Ngày nghỉ việc") if end_date else (current.get("employment_end_date") or datetime.now(VN_TZ).date().isoformat())
        rows = self.client.update("employee_directory", {"email": f"eq.{email}"}, {"active": False, "employment_end_date": end, "updated_at": _now()})
        self.audit(email, "admin", "deactivate", {"active": {"old": True, "new": False}, "employment_end_date": {"old": current.get("employment_end_date"), "new": end}})
        return rows[0] if rows else {**current, "active": False, "employment_end_date": end}

    def reactivate(self, email: str) -> dict[str, Any]:
        email = normalize_email(email)
        current = self._require(email)
        if current.get("active"):
            raise DirectoryError(409, "Nhân viên này đang hoạt động.")
        rows = self.client.update("employee_directory", {"email": f"eq.{email}"}, {"active": True, "employment_end_date": None, "updated_at": _now()})
        self.audit(email, "admin", "reactivate", {"active": {"old": False, "new": True}, "employment_end_date": {"old": current.get("employment_end_date"), "new": None}})
        return rows[0] if rows else {**current, "active": True, "employment_end_date": None}

    # ---------- nhập hàng loạt ----------
    def import_rows(self, rows: list[dict[str, Any]], dry_run: bool = True) -> dict[str, Any]:
        if len(rows) > MAX_IMPORT_ROWS:
            raise DirectoryError(422, f"Mỗi lần nhập tối đa {MAX_IMPORT_ROWS} dòng.")
        existing_rows = self.client.select("employee_directory", {"select": DIRECTORY_COLUMNS, "limit": str(MAX_SCAN_ROWS)})
        existing = {r["email"]: r for r in existing_rows}
        managers = {email: r.get("manager_email") for email, r in existing.items()}
        seen: set[str] = set()
        errors: list[dict[str, Any]] = []
        preview: list[dict[str, Any]] = []
        creates: list[tuple[int, str, dict[str, Any]]] = []
        updates: list[tuple[int, str, dict[str, Any], dict[str, Any]]] = []
        unchanged = 0

        def fail(number: int, email: str, message: str) -> None:
            errors.append({"row": number, "email": email, "message": message})
            preview.append({"row": number, "email": email, "action": "error", "changes": {}})

        for number, raw in enumerate(rows, start=1):
            source = {str(k).strip().casefold(): v for k, v in (raw or {}).items()}
            email_raw = normalize_email(source.get("email"))
            try:
                email = self._check_email(source.get("email"))
                if email in seen:
                    raise DirectoryError(422, "Email bị trùng với một dòng phía trên trong file.")
                seen.add(email)
                fields = clean_fields({k: v for k, v in source.items() if k in IMPORT_COLUMNS and k != "email"}, allow_clear=False, ignore_blank=True)
                current = existing.get(email)
                if current is None:
                    missing = [field_label(k) for k in REQUIRED_FIELDS if not fields.get(k)]
                    if missing:
                        raise DirectoryError(422, "Người mới thiếu: " + ", ".join(missing) + ".")
                self._check_manager(email, fields.get("manager_email"), managers)
            except DirectoryError as error:
                fail(number, email_raw, error.detail)
                continue
            if "manager_email" in fields:
                managers[email] = fields["manager_email"]
            if current is None:
                existing[email] = {"email": email, **fields, "active": True}
                creates.append((number, email, fields))
                preview.append({"row": number, "email": email, "action": "create", "changes": {k: {"old": None, "new": v} for k, v in fields.items()}})
                continue
            changes = diff(current, fields)
            if not changes:
                unchanged += 1
                preview.append({"row": number, "email": email, "action": "unchanged", "changes": {}})
            else:
                updates.append((number, email, changes, {k: c["new"] for k, c in changes.items()}))
                preview.append({"row": number, "email": email, "action": "update", "changes": changes})

        if not dry_run:
            if creates:
                self.client.insert("employee_directory", [{"email": e, **f, "active": True} for _, e, f in creates], returning=False)
            for _, email, _, patch in updates:
                self.client.update("employee_directory", {"email": f"eq.{email}"}, {**patch, "updated_at": _now()})
            for _, email, fields in creates:
                self.audit(email, "admin", "import", {k: {"old": None, "new": v} for k, v in fields.items()})
            for _, email, changes, _ in updates:
                self.audit(email, "admin", "import", changes)
        return {"dry_run": dry_run, "created": len(creates), "updated": len(updates), "unchanged": unchanged,
                "errors": errors, "preview": sorted(preview, key=lambda item: item["row"])[:50]}
