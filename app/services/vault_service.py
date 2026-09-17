from pathlib import Path
from typing import Any

import frontmatter

from app.services.obsidian import KnowledgeChunk, ObsidianLoader


class VaultService:
    """Đọc note đã duyệt từ Obsidian và cung cấp chunk cho Graph-RAG."""

    def __init__(self, vault_path: Path, graph_path: Path):
        self.vault_path = vault_path.resolve()
        self.loader = ObsidianLoader(self.vault_path, graph_path)

    def resolve_note(self, note_name: str) -> Path | None:
        """Chỉ resolve file bên trong vault, tránh path traversal từ wikilink."""
        normalized = note_name.strip().replace("\\", "/")
        if normalized.endswith(".md"):
            normalized = normalized[:-3]
        candidate = (self.vault_path / f"{normalized}.md").resolve()
        if candidate.is_file() and self.vault_path in candidate.parents:
            return candidate
        for path in self.vault_path.rglob("*.md"):
            if path.stem == Path(normalized).name and self.vault_path in path.resolve().parents:
                return path.resolve()
        return None

    def get_approved_chunks(self, note_name: str, filters: dict[str, Any]) -> list[dict[str, Any]]:
        path = self.resolve_note(note_name)
        if path is None:
            return []
        post = frontmatter.load(path)
        metadata = dict(post.metadata)
        if str(metadata.get("status", "")).lower() != "approved":
            return []
        if filters.get("user_department") and str(metadata.get("department")) != filters["user_department"]:
            return []
        if filters.get("version") and str(metadata.get("version")) != str(filters["version"]):
            return []
        chunks = self.loader.chunk_file(path, post.content, metadata)
        return [{"id": item.id, "text": item.text, "metadata": item.metadata} for item in chunks]

    def read_note(self, note_name: str) -> str | None:
        path = self.resolve_note(note_name)
        if path is None:
            return None
        post = frontmatter.load(path)
        if str(post.metadata.get("status", "")).lower() != "approved":
            return None
        return post.content
