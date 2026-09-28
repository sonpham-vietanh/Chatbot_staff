"""Dong bo wiki_obsidian -> Supabase, goi truc tiep AdminService (khong qua HTTP tu
goi chinh minh, vi da nam chung 1 process voi API roi)."""
from __future__ import annotations

from pathlib import Path
from typing import Any

import yaml

from app.services.admin_service import AdminService

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
    vault = Path(vault_path)
    pages = []
    for path in _wiki_md_files(vault):
        meta, body = _parse_frontmatter(path.read_text(encoding="utf-8"))
        if not meta.get("title"):
            continue
        department = _department_for_path(vault, path)
        pages.append({
            "path": str(path.relative_to(vault)),
            "title": meta["title"],
            "department": department,
            "access_level": "manager" if meta.get("confidential") else "staff",
            "content": body.strip(),
        })
    return pages


class WikiSyncService:
    def __init__(self, admin: AdminService, vault_path: str):
        self.admin = admin
        self.vault_path = vault_path

    def sync_all(self, wipe: bool = False, status: str = "approved") -> dict[str, int]:
        pages = collect_wiki_pages(self.vault_path)
        if wipe:
            for note in self.admin.list_notes("all"):
                self.admin.delete_note(note["id"])
        created, failed = 0, 0
        for p in pages:
            try:
                self.admin.create_note(
                    title=p["title"], department=p["department"], content=p["content"],
                    access_level=p["access_level"], status=status, created_by="WikiSync",
                )
                created += 1
            except Exception:
                failed += 1
        return {"created": created, "failed": failed, "total": len(pages)}
