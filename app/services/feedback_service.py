from datetime import datetime, timezone
from typing import Any

from app.services.supabase_client import SupabaseClient

FEEDBACK_STATUSES = ("open", "resolved", "dismissed")


class FeedbackNotFoundError(Exception):
    pass


class FeedbackService:
    """Báo cáo "Báo sai" của nhân viên cho từng câu trả lời của trợ lý (bảng answer_feedback).
    Chỉ backend (service_role) đọc/ghi; người dùng chỉ được gửi, không được xem lại."""

    def __init__(self, client: SupabaseClient):
        self.client = client

    def create(self, user: dict[str, Any], payload: dict[str, Any]) -> dict[str, Any]:
        rows = self.client.insert("answer_feedback", [{
            "user_id": user.get("id"),
            "user_email": user.get("email"),
            "thread_id": payload.get("thread_id"),
            "question": payload["question"],
            "answer": payload["answer"],
            "citations": payload.get("citations") or [],
            "reason": payload["reason"],
            "note": payload.get("note") or None,
        }])
        return rows[0]

    def list(self, status: str = "open", limit: int = 200) -> list[dict[str, Any]]:
        params = {"select": "*", "order": "created_at.desc", "limit": str(limit)}
        if status != "all":
            params["status"] = f"eq.{status}"
        return self.client.select("answer_feedback", params)

    def count_open(self) -> int:
        return len(self.client.select("answer_feedback", {"select": "id", "status": "eq.open"}))

    def set_status(self, feedback_id: str, status: str) -> dict[str, Any]:
        handled_at = None if status == "open" else datetime.now(timezone.utc).isoformat()
        rows = self.client.update(
            "answer_feedback", {"id": f"eq.{feedback_id}"}, {"status": status, "handled_at": handled_at}
        )
        if not rows:
            raise FeedbackNotFoundError(f"Không tìm thấy báo cáo: {feedback_id}")
        return rows[0]
