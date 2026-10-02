from datetime import datetime, timezone
import ipaddress
import logging
import re
import secrets
from typing import Any
from urllib.parse import urlsplit

from app.services.supabase_client import SupabaseClient

logger = logging.getLogger(__name__)

DEFAULT_PORTS = {"http": 80, "https": 443}
_HOSTNAME_LABEL = r"[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?"
_HOSTNAME = re.compile(rf"{_HOSTNAME_LABEL}(?:\.{_HOSTNAME_LABEL})*")


class ApiKeyNotFoundError(Exception):
    pass


def normalize_origin(value: str | None) -> str | None:
    """Đưa origin về đúng dạng trình duyệt gửi trong header Origin (chữ thường, không
    port mặc định, không dấu '/' cuối) để so khớp không lệch vì cách gõ. Trả None nếu
    không phải origin http(s) hợp lệ (có đường dẫn/query, 'null', thiếu giao thức...)."""
    if not value:
        return None
    try:
        parts = urlsplit(value.strip())
        port = parts.port
    except ValueError:
        return None
    scheme = parts.scheme.lower()
    host = parts.hostname
    if scheme not in DEFAULT_PORTS or not host or port == 0:
        return None
    if parts.path not in ("", "/") or parts.query or parts.fragment or parts.username:
        return None
    if ":" in host:
        try:
            host = f"[{ipaddress.IPv6Address(host).compressed}]"
        except ValueError:
            return None
    elif not _HOSTNAME.fullmatch(host):
        # Giá trị này đi thẳng vào header CSP frame-ancestors — ký tự lạ (dấu cách, ';',
        # '*', dấu nháy...) sẽ mở cho mọi trang nhúng hoặc chèn thêm chỉ thị CSP.
        return None
    return f"{scheme}://{host}" + (f":{port}" if port is not None and port != DEFAULT_PORTS[scheme] else "")


class ApiKeyService:
    """Quản lý API key cho phép các trang nội bộ khác nhúng (iframe) chatbot này —
    khoá mặc định chỉ cho phép embed từ đúng domain đã khai báo, chặn site lạ nhúng."""

    def __init__(self, client: SupabaseClient):
        self.client = client

    def list_keys(self) -> list[dict[str, Any]]:
        return self.client.select("api_keys", {"select": "*", "order": "created_at.desc"})

    def create_key(self, label: str, allowed_origin: str) -> dict[str, Any]:
        origin = normalize_origin(allowed_origin)
        if not origin:
            raise ValueError(f"Origin không hợp lệ: {allowed_origin}")
        key = "vas_" + secrets.token_urlsafe(24)
        rows = self.client.insert("api_keys", [{
            "label": label,
            "key": key,
            "allowed_origin": origin,
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
        """last_used_at chỉ để admin theo dõi — best-effort, lỗi ghi không được làm hỏng
        request của bên tích hợp (nhưng phải ghi log để còn biết mà sửa)."""
        try:
            self.client.update(
                "api_keys", {"id": f"eq.{key_id}"}, {"last_used_at": datetime.now(timezone.utc).isoformat()}
            )
        except Exception:
            logger.warning("Không cập nhật được last_used_at cho API key %s", key_id, exc_info=True)
