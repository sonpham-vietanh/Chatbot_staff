"""Dong bo wiki_obsidian -> Supabase, goi truc tiep AdminService (khong qua HTTP tu
goi chinh minh, vi da nam chung 1 process voi API roi)."""
from __future__ import annotations

import logging
from pathlib import Path
from typing import Any

import yaml

from app.services.admin_service import AdminService

logger = logging.getLogger(__name__)

DOMAIN_TO_DEPARTMENT = {
    "nhan-su": "HR",
    "tai-chinh": "Finance",
    "hoc-thuat-chuong-trinh": "Academic",
    "van-hanh-co-so": "Admin",
    "marketing-tuyen-sinh": "Admin",
    "sales-cskh": "Admin",
    "doi-thu-canh-tranh": "Admin",
}


# Cau truc vault tu 2026-09-24: moi domain la 1 thu muc goc rieng (co wiki/ rieng),
# "core/" la thu muc dung chung - xem CLAUDE.md muc 1. Cac thu muc khac o goc vault
# khong phai domain (git, config Obsidian, scaffold cong cu AI khac...).
NON_DOMAIN_DIRS = {"core", ".git", ".obsidian", ".claude", ".opencode", ".copilot", "copilot"}


def _parse_frontmatter(text: str) -> tuple[dict, str]:
    if not text.startswith("---"):
        return {}, text
    parts = text.split("---", 2)
    if len(parts) < 3:
        return {}, text
    meta = yaml.safe_load(parts[1]) or {}
    return meta, parts[2].lstrip("\n")


def _department_for_path(vault: Path, path: Path) -> str:
    """Phong ban quyet dinh boi THU MUC VAT LY (core/ vs <domain>/wiki/), khong phai
    frontmatter `domain:` cua trang - frontmatter co the liet ke nhieu domain lien quan
    (vd 1 entity lanh dao ca 2 mang) nhung trang do van chi vat ly nam o 1 cho duy nhat.
    Dung path la nguon su that duy nhat, dung voi nguyen tac phan quyen cua CLAUDE.md."""
    top = path.relative_to(vault).parts[0]
    if top == "core":
        return "Unassigned"
    return DOMAIN_TO_DEPARTMENT.get(top, "Unassigned")


def _wiki_md_files(vault: Path) -> list[Path]:
    files: list[Path] = []
    core_dir = vault / "core"
    if core_dir.is_dir():
        files.extend(core_dir.rglob("*.md"))
    if vault.is_dir():
        for domain_dir in vault.iterdir():
            if not domain_dir.is_dir() or domain_dir.name in NON_DOMAIN_DIRS or domain_dir.name.startswith("."):
                continue
            wiki_dir = domain_dir / "wiki"
            if wiki_dir.is_dir():
                files.extend(wiki_dir.rglob("*.md"))
    return sorted(files)


def collect_wiki_pages(vault_path: str) -> list[dict[str, Any]]:
    return collect_wiki(vault_path)[0]


def collect_wiki(vault_path: str) -> tuple[list[dict[str, Any]], set[str]]:
    """Trả (các trang đọc được, đường dẫn các file TỒN TẠI nhưng không đọc được).

    File không đọc được (Drive đang đồng bộ dở, frontmatter hỏng, sai mã hoá) khác với file đã bị xoá: đồng bộ không được coi
    chúng là "trang đã biến mất" và xoá mất note đang chạy ổn trên chatbot."""
    vault = Path(vault_path)
    pages = []
    unreadable: set[str] = set()
    for path in _wiki_md_files(vault):
        try:
            meta, body = _parse_frontmatter(path.read_text(encoding="utf-8-sig"))  # utf-8-sig: nhận cả file có BOM
        except (yaml.YAMLError, UnicodeDecodeError, OSError):
            unreadable.add(str(path.relative_to(vault)).replace("\\", "/"))
            continue  # 1 file hỏng frontmatter không được làm hỏng cả lần đồng bộ
        if not isinstance(meta, dict) or not meta.get("title"):
            unreadable.add(str(path.relative_to(vault)).replace("\\", "/"))
            continue
        if meta.get("chatbot") is False:
            continue  # trang hướng dẫn nội bộ (vd. cài Drive) — người dùng chủ động loại khỏi chatbot
        department = _department_for_path(vault, path)
        pages.append({
            "path": str(path.relative_to(vault)),
            "title": meta["title"],
            "department": department,
            "access_level": "manager" if meta.get("confidential") else "staff",
            "content": body.strip(),
        })
    return pages, unreadable


VAULT_PREFIX = "vault:"
"""source_file của note sinh từ wiki = "vault:<đường dẫn trong vault>" — dấu hiệu duy nhất để
phân biệt với note tạo trong /admin (source_file trống hoặc là tên file upload)."""
LEGACY_CREATORS = {"Admin", "WikiSync"}
"""Các trang wiki đã đẩy lên trước khi có dấu "vault:" mang created_by = "Admin" và
source_file rỗng — được nhận lại theo đúng tiêu đề ở lần đồng bộ đầu tiên."""
SYNCED_FIELDS = ("title", "department", "access_level", "content")


class WikiSyncService:
    """Đồng bộ wiki Obsidian -> knowledge_notes theo kiểu cập nhật từng note, KHÔNG xoá sạch
    rồi tạo lại: note tạo/sửa trong /admin và note [CẦN BỔ SUNG] không bao giờ bị đụng tới.
    Note gốc từ wiki thì wiki là nguồn sự thật — sửa trong Obsidian; sửa ở /admin sẽ bị ghi
    đè ở lần đồng bộ sau nếu nội dung wiki khác."""

    def __init__(self, admin: AdminService, vault_path: str, recorder=None):
        self.admin = admin
        self.vault_path = vault_path
        self.recorder = recorder  # recorder(note, "sync") ghi lịch sử để bản leader sửa trên web vẫn khôi phục được

    def _record(self, note: dict[str, Any]) -> None:
        if self.recorder is None:
            return
        try:
            self.recorder(note, "sync")
        except Exception:
            pass  # lịch sử lỗi không được làm hỏng đồng bộ

    def sync_all(self, status: str = "approved") -> dict[str, int]:
        pages, unreadable = collect_wiki(self.vault_path)
        notes = self.admin.list_notes("all")
        by_source = {n["source_file"]: n for n in notes if str(n.get("source_file") or "").startswith(VAULT_PREFIX)}
        legacy_by_title: dict[str, dict[str, Any]] = {}
        for note in notes:
            if not note.get("source_file") and note.get("created_by") in LEGACY_CREATORS:
                legacy_by_title.setdefault(note["title"], note)

        result = {"created": 0, "updated": 0, "unchanged": 0, "deleted": 0, "failed": 0, "total": len(pages)}
        seen: set[str] = set()
        for page in pages:
            source = VAULT_PREFIX + page["path"].replace("\\", "/")
            seen.add(source)
            existing = by_source.get(source) or legacy_by_title.pop(page["title"], None)
            try:
                if existing is None:
                    created = self.admin.create_note(
                        title=page["title"], department=page["department"], content=page["content"],
                        access_level=page["access_level"], status=status, created_by="WikiSync",
                        source_file=source,
                    )
                    self._record(created)
                    result["created"] += 1
                    continue
                changed = {field: page[field] for field in SYNCED_FIELDS if existing.get(field) != page[field]}
                if existing.get("source_file") != source:
                    changed["source_file"] = source
                if not changed:
                    result["unchanged"] += 1
                    continue
                # Giữ nguyên trạng thái duyệt/từ chối mà admin đã đặt cho note.
                saved = self.admin.save_note(existing["id"], changed)
                if any(field in changed for field in ("title", "department", "content", "access_level")):
                    self._record(saved)
                result["updated"] += 1
            except Exception:
                result["failed"] += 1

        # Trang đã bị xoá khỏi wiki -> xoá note tương ứng (chỉ note mang dấu "vault:").
        gone = {s: n for s, n in by_source.items() if s not in seen and s[len(VAULT_PREFIX):] not in unreadable}
        # Van an toàn: vault rỗng/chưa tải xong, hoặc sắp xoá quá nhiều cùng lúc thì nhiều khả năng Drive đang đồng bộ dở chứ
        # không phải người dùng xoá thật — giữ nguyên và báo trong kết quả.
        if gone and (not pages or len(gone) > max(3, len(by_source) * 0.3)):
            logger.warning("Bỏ qua việc xoá %d note vì vault có vẻ chưa đồng bộ đủ (%d trang đọc được)", len(gone), len(pages))
            result["skipped_delete"] = len(gone)
            gone = {}
        for source, note in gone.items():
            if True:
                try:
                    self.admin.delete_note(note["id"])
                    result["deleted"] += 1
                except Exception:
                    result["failed"] += 1
        return result
