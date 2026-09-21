from datetime import datetime, timezone
import secrets
from typing import Any

from app.services.supabase_client import SupabaseClient


class ApiKeyNotFoundError(Exception):
    pass


class ApiKeyService:
    """Quản lý API key cho phép các trang nội bộ khác nhúng (iframe) chatbot này —
    khoá mặc định chỉ cho phép embed từ đúng domain đã khai báo, chặn site lạ nhúng."""

    def __init__(self, client: SupabaseClient):
        self.client = client

    def list_keys(self) -> list[dict[str, Any]]:
        return self.client.select("api_keys", {"select": "*", "order": "created_at.desc"})

    def create_key(self, label: str, allowed_origin: str) -> dict[str, Any]:
        key = "vas_" + secrets.token_urlsafe(24)
        rows = self.client.insert("api_keys", [{
            "label": label,
            "key": key,
            "allowed_origin": allowed_origin.rstrip("/"),
        }])
        return rows[0]

    def revoke_key(self, key_id: str) -> None:
        rows = self.client.update("api_keys", {"id": f"eq.{key_id}"}, {"status": "revoked"})
        if not rows:
            raise ApiKeyNotFoundError(f"Không tìm thấy API key: {key_id}")

    def delete_key(self, key_id: str) -> None:
        self.client.delete("api_keys", {"id": f"eq.{key_id}"})

    def get_active_key(self, key: str) -> dict[str, Any] | None:
        rows = self.client.select("api_keys", {
            "select": "*", "key": f"eq.{key}", "status": "eq.active",
        })
        return rows[0] if rows else None

    def touch_last_used(self, key_id: str) -> None:
        self.client.update(
            "api_keys", {"id": f"eq.{key_id}"}, {"last_used_at": datetime.now(timezone.utc).isoformat()}
        )
