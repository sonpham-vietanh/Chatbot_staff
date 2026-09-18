from datetime import datetime, timezone
from typing import Any

from app.rag.chunking import chunk_content
from app.rag.embeddings import EmbeddingProvider
from app.services.supabase_client import SupabaseClient


class NoteNotFoundError(Exception):
    pass


class AdminService:
    """CRUD note trong Supabase (knowledge_notes) + đồng bộ chunk/embedding
    (knowledge_chunks) ngay tại thời điểm ghi — không cần bước reindex nền riêng,
    loại bỏ hẳn lớp watcher/race-condition mà bản file-based Obsidian từng gặp."""

    def __init__(self, client: SupabaseClient, embedding_provider: EmbeddingProvider):
        self.client = client
        self.embedding_provider = embedding_provider

    def list_notes(self, status: str | None = None) -> list[dict[str, Any]]:
        params = {"select": "*", "order": "created_at.desc"}
        if status and status != "all":
            params["status"] = f"eq.{status}"
        rows = self.client.select("knowledge_notes", params)
        for row in rows:
            row["preview"] = (row.get("content") or "").strip().replace("\n", " ")[:200]
        return rows

    def get_note(self, note_id: str) -> dict[str, Any]:
        rows = self.client.select("knowledge_notes", {"select": "*", "id": f"eq.{note_id}"})
        if not rows:
            raise NoteNotFoundError(f"Không tìm thấy note: {note_id}")
        return rows[0]

    def create_note(self, title: str, department: str, content: str,
                     access_level: str = "staff", status: str = "draft",
                     created_by: str = "Admin") -> dict[str, Any]:
        rows = self.client.insert("knowledge_notes", [{
            "title": title,
            "department": department or "Unassigned",
            "content": content,
            "access_level": access_level,
            "status": status,
            "created_by": created_by,
        }])
        note = rows[0]
        self._sync_chunks(note)
        return note

    def save_note(self, note_id: str, patch: dict[str, Any]) -> dict[str, Any]:
        patch = {**patch, "updated_at": datetime.now(timezone.utc).isoformat()}
        rows = self.client.update("knowledge_notes", {"id": f"eq.{note_id}"}, patch)
        if not rows:
            raise NoteNotFoundError(f"Không tìm thấy note: {note_id}")
        note = rows[0]
        self._sync_chunks(note)
        return note

    def set_status(self, note_id: str, status: str, reviewed_by: str | None = None) -> dict[str, Any]:
        patch = {
            "status": status,
            "reviewed_at": datetime.now(timezone.utc).isoformat(),
        }
        if reviewed_by:
            patch["reviewed_by"] = reviewed_by
        return self.save_note(note_id, patch)

    def delete_note(self, note_id: str) -> None:
        self.client.delete("knowledge_notes", {"id": f"eq.{note_id}"})

    def _sync_chunks(self, note: dict[str, Any]) -> None:
        """Cắt lại note thành chunk, embed hàng loạt, rồi ghi đè toàn bộ chunk cũ của note
        này bằng 1 lệnh RPC (xoá + insert atomically trong DB)."""
        pieces = chunk_content(note["content"])
        if not pieces:
            self.client.rpc("sync_knowledge_chunks", {"p_note_id": note["id"], "p_chunks": []})
            return
        embeddings = self.embedding_provider.embed_batch([piece["text"] for piece in pieces])
        payload = [
            {"chunk_index": index, "heading": piece["heading"], "text": piece["text"], "embedding": embedding}
            for index, (piece, embedding) in enumerate(zip(pieces, embeddings))
        ]
        self.client.rpc("sync_knowledge_chunks", {"p_note_id": note["id"], "p_chunks": payload})
