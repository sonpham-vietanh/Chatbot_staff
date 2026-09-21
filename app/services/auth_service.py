import httpx


class AuthError(Exception):
    def __init__(self, status_code: int, detail: str):
        self.status_code = status_code
        self.detail = detail
        super().__init__(detail)


class AuthService:
    """Proxy mỏng tới Supabase Auth REST (GoTrue) — dùng service_role key làm apikey,
    không cần thêm biến môi trường nào ngoài SUPABASE_URL/SUPABASE_SERVICE_KEY đã có sẵn.
    get_user() chạy trên MỌI request có xác thực nên dùng httpx.Client tái sử dụng kết nối
    thay vì bắt tay TCP/TLS mới mỗi lần — instance này là singleton dùng chung cả app."""

    def __init__(self, url: str, service_key: str):
        self.base_url = url.rstrip("/")
        self.service_key = service_key
        self._http = httpx.Client(timeout=30)

    def _headers(self) -> dict[str, str]:
        return {"apikey": self.service_key, "Content-Type": "application/json"}

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
