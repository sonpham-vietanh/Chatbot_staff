from datetime import datetime, timezone
import json
from pathlib import Path
import re
from typing import Any

import frontmatter


class NoteNotFoundError(Exception):
    pass


class InvalidNotePathError(Exception):
    pass


class AdminService:
    """Cho phép HR/admin duyệt, sửa, từ chối note Obsidian qua API thay vì mở Obsidian thủ công."""

    EXCLUDED_DIRS = {".obsidian", ".staff_uploads"}

    def __init__(self, vault_path: Path):
        self.vault_path = vault_path.resolve()

    def _iter_notes(self):
        for path in sorted(self.vault_path.rglob("*.md")):
            if any(part in self.EXCLUDED_DIRS for part in path.relative_to(self.vault_path).parts):
                continue
            yield path

    def _resolve(self, relative_path: str) -> Path:
        candidate = (self.vault_path / relative_path).resolve()
        if self.vault_path != candidate and self.vault_path not in candidate.parents:
            raise InvalidNotePathError("Đường dẫn note không hợp lệ")
        if not candidate.is_file():
            raise NoteNotFoundError(f"Không tìm thấy note: {relative_path}")
        return candidate

    def list_notes(self, status: str | None = None) -> list[dict[str, Any]]:
        results = []
        for path in self._iter_notes():
            try:
                post = frontmatter.load(path)
            except (OSError, UnicodeDecodeError):
                continue
            metadata = dict(post.metadata)
            note_status = str(metadata.get("status", "")).lower()
            if status and status != "all" and note_status != status:
                continue
            results.append({
                "path": str(path.relative_to(self.vault_path)).replace("\\", "/"),
                "title": str(metadata.get("title", path.stem)),
                "department": str(metadata.get("department", "unknown")),
                "owner": str(metadata.get("owner", "unknown")),
                "status": note_status or "unknown",
                "created_by": str(metadata.get("created_by", "")),
                "created_at": str(metadata.get("created_at", "")),
                "version": str(metadata.get("version", "unknown")),
                "access_level": str(metadata.get("access_level", "staff")),
                "preview": post.content.strip().replace("\n", " ")[:200],
            })
        results.sort(key=lambda item: item["created_at"], reverse=True)
        return results

    def get_note(self, relative_path: str) -> dict[str, Any]:
        path = self._resolve(relative_path)
        post = frontmatter.load(path)
        return {
            "path": relative_path,
            "metadata": dict(post.metadata),
            "content": post.content,
        }

    def save_note(self, relative_path: str, metadata: dict[str, Any], content: str) -> None:
        path = self._resolve(relative_path)
        lines = ["---"]
        for key, value in metadata.items():
            lines.append(f"{key}: {json.dumps(str(value), ensure_ascii=False)}")
        lines.extend(["---", "", content.strip(), ""])
        path.write_text("\n".join(lines), encoding="utf-8")

    def set_status(self, relative_path: str, status: str, reviewed_by: str | None = None) -> None:
        path = self._resolve(relative_path)
        post = frontmatter.load(path)
        post.metadata["status"] = status
        post.metadata["reviewed_at"] = datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")
        if reviewed_by:
            post.metadata["reviewed_by"] = reviewed_by
        self.save_note(relative_path, post.metadata, post.content)

    def delete_note(self, relative_path: str) -> None:
        path = self._resolve(relative_path)
        path.unlink()

    def create_note(self, title: str, department: str, content: str,
                     access_level: str = "staff", status: str = "draft",
                     created_by: str = "Admin") -> str:
        """Tạo note mới từ đầu (không phải upload file). Trả về relative_path vừa tạo."""
        slug = re.sub(r"[^a-zA-Z0-9_-]+", "_", title).strip("_")[:80] or "note"
        identifier = 1
        candidate = self.vault_path / f"{slug}.md"
        while candidate.exists():
            identifier += 1
            candidate = self.vault_path / f"{slug}_{identifier}.md"
        timestamp = datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")
        metadata = {
            "title": title,
            "department": department or "Unassigned",
            "owner": "hr@vietanh.edu.vn",
            "status": status,
            "created_by": created_by,
            "created_at": timestamp,
            "version": "0.1",
            "access_level": access_level,
        }
        lines = ["---"]
        for key, value in metadata.items():
            lines.append(f"{key}: {json.dumps(str(value), ensure_ascii=False)}")
        lines.extend(["---", "", content.strip(), ""])
        candidate.write_text("\n".join(lines), encoding="utf-8")
        return str(candidate.relative_to(self.vault_path)).replace("\\", "/")
