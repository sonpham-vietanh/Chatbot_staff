from typing import Any

import httpx


class SupabaseClient:
    """Wrapper mỏng quanh REST (PostgREST) + RPC của Supabase, dùng service_role key.
    Không cần thư viện supabase-py hay kết nối Postgres trực tiếp — chỉ cần URL + secret key.
    Dùng 1 httpx.Client tái sử dụng (keep-alive) thay vì tạo kết nối TCP/TLS mới cho mỗi
    request — instance này là singleton dùng chung cả app nên an toàn để giữ connection pool."""

    def __init__(self, url: str, service_key: str):
        self.base_url = url.rstrip("/")
        self.service_key = service_key
        self._http = httpx.Client(timeout=30)

    def _headers(self, prefer: str | None = None) -> dict[str, str]:
        headers = {
            "apikey": self.service_key,
            "Authorization": f"Bearer {self.service_key}",
            "Content-Type": "application/json",
        }
        if prefer:
            headers["Prefer"] = prefer
        return headers

    def select(self, table: str, params: dict[str, str] | None = None) -> list[dict[str, Any]]:
        response = self._http.get(f"{self.base_url}/rest/v1/{table}", headers=self._headers(), params=params, timeout=30)
        response.raise_for_status()
        return response.json()

    def insert(self, table: str, rows: list[dict[str, Any]], returning: bool = True) -> list[dict[str, Any]] | None:
        prefer = "return=representation" if returning else "return=minimal"
        response = self._http.post(f"{self.base_url}/rest/v1/{table}", headers=self._headers(prefer), json=rows, timeout=60)
        response.raise_for_status()
        return response.json() if returning else None

    def update(self, table: str, params: dict[str, str], patch: dict[str, Any]) -> list[dict[str, Any]]:
        response = self._http.patch(
            f"{self.base_url}/rest/v1/{table}", headers=self._headers("return=representation"),
            params=params, json=patch, timeout=30,
        )
        response.raise_for_status()
        return response.json()

    def delete(self, table: str, params: dict[str, str]) -> None:
        response = self._http.delete(f"{self.base_url}/rest/v1/{table}", headers=self._headers(), params=params, timeout=30)
        response.raise_for_status()

    def rpc(self, function_name: str, payload: dict[str, Any]) -> Any:
        response = self._http.post(
            f"{self.base_url}/rest/v1/rpc/{function_name}", headers=self._headers(), json=payload, timeout=60
        )
        response.raise_for_status()
        if not response.content:
            return None
        return response.json()
