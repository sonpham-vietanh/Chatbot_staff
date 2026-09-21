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

    def create_thread_with_id(self, thread_id: str, user_id: str, title: str = DEFAULT_TITLE) -> None:
        """Nhận id do caller tự sinh (uuid4) thay vì để DB default — nhờ vậy route chat
        có thread_id để trả về ngay mà không phải chờ round-trip insert hoàn tất."""
        self.client.insert(
            "chat_threads", [{"id": thread_id, "user_id": user_id, "title": title or DEFAULT_TITLE}], returning=False
        )

    def thread_belongs_to(self, thread_id: str, user_id: str) -> bool:
        return self.get_thread(thread_id, user_id) is not None

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

    def add_turn(self, thread_id: str, question: str, answer: str) -> None:
        """Lưu cả 2 tin nhắn (user + assistant) trong 1 lần insert, cộng 1 lần update
        updated_at — thay vì 4 round-trip riêng lẻ như trước, giảm độ trễ lưu lịch sử."""
        self.client.insert("chat_messages", [
            {"thread_id": thread_id, "role": "user", "content": question},
            {"thread_id": thread_id, "role": "assistant", "content": answer},
        ], returning=False)
        self.client.update("chat_threads", {"id": f"eq.{thread_id}"}, {"updated_at": datetime.now(timezone.utc).isoformat()})

    def delete_thread(self, thread_id: str, user_id: str) -> None:
        self.client.delete("chat_threads", {"id": f"eq.{thread_id}", "user_id": f"eq.{user_id}"})
