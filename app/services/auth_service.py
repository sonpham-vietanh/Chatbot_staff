import base64
import json

import httpx


def is_public_auth_key(api_key: str | None) -> bool:
    if not api_key:
        return False
    if api_key.startswith("sb_publishable_"):
        return True
    if api_key.startswith("sb_secret_"):
        return False
    if not api_key.startswith("eyJ"):
        return False
    try:
        payload = api_key.split(".")[1]
        payload += "=" * (-len(payload) % 4)
        claims = json.loads(base64.urlsafe_b64decode(payload))
    except (IndexError, ValueError, json.JSONDecodeError):
        return False
    return claims.get("role") == "anon"


class AuthError(Exception):
    def __init__(self, status_code: int, detail: str):
        self.status_code = status_code
        self.detail = detail
        super().__init__(detail)


class AuthService:
    """Proxy mỏng tới Supabase Auth REST (GoTrue), xác thực bằng anon/publishable key.
    get_user() chạy trên MỌI request có xác thực nên dùng httpx.Client tái sử dụng kết nối
    thay vì bắt tay TCP/TLS mới mỗi lần — instance này là singleton dùng chung cả app."""

    def __init__(self, url: str, api_key: str | None):
        self.base_url = url.rstrip("/")
        self.api_key = api_key
        self._http = httpx.Client(timeout=30)

    def _headers(self) -> dict[str, str]:
        if not is_public_auth_key(self.api_key):
            raise AuthError(503, "SUPABASE_ANON_KEY phải là anon/publishable key, không dùng secret/service_role key.")
        return {"apikey": self.api_key, "Content-Type": "application/json"}

    def signup(self, email: str, password: str, display_name: str) -> dict:
        response = self._http.post(
            f"{self.base_url}/auth/v1/signup",
            headers=self._headers(),
            json={"email": email, "password": password, "data": {"display_name": display_name}},
            timeout=30,
        )
        return self._parse(response)

    def login(self, email: str, password: str) -> dict:
        response = self._http.post(
            f"{self.base_url}/auth/v1/token?grant_type=password",
            headers=self._headers(),
            json={"email": email, "password": password},
            timeout=30,
        )
        return self._parse(response)

    def refresh(self, refresh_token: str) -> dict:
        response = self._http.post(
            f"{self.base_url}/auth/v1/token?grant_type=refresh_token",
            headers=self._headers(),
            json={"refresh_token": refresh_token},
            timeout=30,
        )
        return self._parse(response)

    def get_user(self, access_token: str) -> dict:
        response = self._http.get(
            f"{self.base_url}/auth/v1/user",
            headers={**self._headers(), "Authorization": f"Bearer {access_token}"},
            timeout=15,
        )
        return self._parse(response)

    @staticmethod
    def _parse(response: httpx.Response) -> dict:
        body = response.json() if response.content else {}
        if response.status_code >= 400:
            detail = body.get("error_description") or body.get("msg") or body.get("error") or "Yêu cầu xác thực thất bại"
            raise AuthError(response.status_code, detail)
        return body
