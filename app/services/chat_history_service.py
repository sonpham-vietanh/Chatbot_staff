from datetime import datetime, timezone
from typing import Any

from app.services.supabase_client import SupabaseClient

DEFAULT_TITLE = "Cuộc trò chuyện mới"


class ChatHistoryService:
    """Lưu lịch sử chat theo user_id (Supabase Auth) qua SupabaseClient service_role —
    không dùng RLS, giống pattern của AdminService/chat_logs."""

    def __init__(self, client: SupabaseClient):
        self.client = client

    def list_threads(self, user_id: str) -> list[dict[str, Any]]:
        return self.client.select("chat_threads", {
            "user_id": f"eq.{user_id}",
            "select": "id,title,updated_at",
            "order": "updated_at.desc",
        })

    def create_thread(self, user_id: str, title: str = DEFAULT_TITLE) -> dict[str, Any]:
        rows = self.client.insert("chat_threads", [{"user_id": user_id, "title": title or DEFAULT_TITLE}])
        return rows[0]

    def get_thread(self, thread_id: str, user_id: str) -> dict[str, Any] | None:
        rows = self.client.select("chat_threads", {
            "id": f"eq.{thread_id}",
            "user_id": f"eq.{user_id}",
            "select": "id,title,updated_at",
        })
        return rows[0] if rows else None

    def list_messages(self, thread_id: str, user_id: str) -> list[dict[str, Any]]:
        if not self.get_thread(thread_id, user_id):
            return []
        return self.client.select("chat_messages", {
            "thread_id": f"eq.{thread_id}",
            "select": "id,role,content,created_at",
            "order": "created_at.asc",
        })

    def add_message(self, thread_id: str, role: str, content: str) -> None:
        self.client.insert("chat_messages", [{"thread_id": thread_id, "role": role, "content": content}], returning=False)
        self.client.update("chat_threads", {"id": f"eq.{thread_id}"}, {"updated_at": datetime.now(timezone.utc).isoformat()})

    def delete_thread(self, thread_id: str, user_id: str) -> None:
        self.client.delete("chat_threads", {"id": f"eq.{thread_id}", "user_id": f"eq.{user_id}"})
