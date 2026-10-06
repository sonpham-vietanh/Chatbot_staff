"""Đăng nhập 1 lần từ Major OS: người dùng đã đăng nhập OS (Google @truongvietanh.com) bấm sang
chatbot thì vào thẳng, không gặp màn đăng nhập lần hai.

Luồng:
1. Backend Major OS ký một JWT HS256 ngắn hạn bằng khoá bí mật dùng chung (OS_SSO_SECRET),
   claims: email, iat, exp (tối đa 5 phút), jti (chuỗi ngẫu nhiên), iss = "major-os".
2. OS chuyển trình duyệt tới https://<chatbot>/sso/os?token=<jwt>.
3. Chatbot kiểm tra chữ ký, hạn, email thuộc domain công ty, jti chưa dùng; rồi nhờ Supabase
   Auth (service key) tạo magic link cho email đó và tự xác minh luôn ở backend -> nhận
   access/refresh token của chính Supabase -> trả về trình duyệt qua URL fragment (#...),
   phần này không bao giờ được gửi lên server hay ghi vào log.
Khoá dùng chung KHÔNG BAO GIỜ được đưa xuống trình duyệt — chỉ backend OS và backend chatbot biết.
"""
from __future__ import annotations

import base64
import hashlib
import hmac
import json
import threading
import time

import httpx

MAX_TOKEN_LIFETIME = 300
CLOCK_SKEW = 30
ISSUER = "major-os"


class SsoError(Exception):
    pass


def _b64url_decode(part: str) -> bytes:
    return base64.urlsafe_b64decode(part + "=" * (-len(part) % 4))


def _b64url_encode(raw: bytes) -> str:
    return base64.urlsafe_b64encode(raw).rstrip(b"=").decode("ascii")


def sign_token(claims: dict, secret: str) -> str:
    """Ký token giống cách backend Major OS phải làm — dùng cho test và tài liệu mẫu."""
    header = _b64url_encode(json.dumps({"alg": "HS256", "typ": "JWT"}, separators=(",", ":")).encode())
    payload = _b64url_encode(json.dumps(claims, separators=(",", ":")).encode())
    signature = hmac.new(secret.encode(), f"{header}.{payload}".encode(), hashlib.sha256).digest()
    return f"{header}.{payload}.{_b64url_encode(signature)}"


class _UsedTokens:
    """Chặn dùng lại cùng 1 token (jti) trong thời gian còn hạn. Lưu trong bộ nhớ từng worker:
    token chỉ sống vài phút và đi thẳng OS -> trình duyệt, nên đủ cho mục đích này."""

    def __init__(self):
        self._seen: dict[str, float] = {}
        self._lock = threading.Lock()

    def claim(self, jti: str, expires_at: float) -> bool:
        now = time.time()
        with self._lock:
            for key in [k for k, exp in self._seen.items() if exp < now]:
                del self._seen[key]
            if jti in self._seen:
                return False
            self._seen[jti] = expires_at
            return True


class SsoService:
    def __init__(self, supabase_url: str, service_key: str | None, anon_key: str | None,
                 secret: str | None, allowed_domains: str | None):
        self.base_url = (supabase_url or "").rstrip("/")
        self.service_key = service_key
        self.anon_key = anon_key
        self.secret = secret
        self.allowed = {d.strip().casefold().lstrip("@") for d in (allowed_domains or "").split(",") if d.strip()}
        self.used = _UsedTokens()
        self._http = httpx.Client(timeout=20)

    @property
    def enabled(self) -> bool:
        return bool(self.secret and len(self.secret) >= 32 and self.service_key and self.anon_key and self.allowed)

    def verify_token(self, token: str) -> str:
        """Trả về email đã chuẩn hoá nếu token hợp lệ; ném SsoError nếu không."""
        if not self.enabled:
            raise SsoError("Đăng nhập qua Major OS chưa được cấu hình.")
        try:
            header_b64, payload_b64, signature_b64 = token.split(".")
            header = json.loads(_b64url_decode(header_b64))
            claims = json.loads(_b64url_decode(payload_b64))
            signature = _b64url_decode(signature_b64)
        except (ValueError, json.JSONDecodeError) as error:
            raise SsoError("Mã đăng nhập không đúng định dạng.") from error
        if not isinstance(header, dict) or not isinstance(claims, dict):
            raise SsoError("Mã đăng nhập không đúng định dạng.")
        if header.get("alg") != "HS256":
            raise SsoError("Thuật toán ký không được hỗ trợ.")
        expected = hmac.new(self.secret.encode(), f"{header_b64}.{payload_b64}".encode(), hashlib.sha256).digest()
        if not hmac.compare_digest(signature, expected):
            raise SsoError("Chữ ký không hợp lệ.")
        if claims.get("iss") != ISSUER:
            raise SsoError("Nguồn phát hành mã không hợp lệ.")
        now = time.time()
        try:
            iat, exp = float(claims["iat"]), float(claims["exp"])
        except (KeyError, TypeError, ValueError) as error:
            raise SsoError("Mã đăng nhập thiếu thời hạn.") from error
        if exp < now - CLOCK_SKEW:
            raise SsoError("Mã đăng nhập đã hết hạn, vui lòng bấm lại từ Major OS.")
        if iat > now + CLOCK_SKEW or exp - iat > MAX_TOKEN_LIFETIME:
            raise SsoError("Thời hạn mã đăng nhập không hợp lệ.")
        jti = str(claims.get("jti") or "")
        if len(jti) < 16:
            raise SsoError("Mã đăng nhập thiếu jti.")
        email = str(claims.get("email") or "").strip().casefold()
        if "@" not in email or email.rsplit("@", 1)[1] not in self.allowed:
            raise SsoError("Email không thuộc domain công ty.")
        if not self.used.claim(jti, exp + CLOCK_SKEW):
            raise SsoError("Mã đăng nhập đã được dùng, vui lòng bấm lại từ Major OS.")
        return email

    def create_session(self, email: str) -> dict:
        """Tạo phiên Supabase cho email (tạo tài khoản nếu chưa có) mà không gửi email nào."""
        admin_headers = {"apikey": self.service_key, "Authorization": f"Bearer {self.service_key}"}
        link = self._http.post(f"{self.base_url}/auth/v1/admin/generate_link", headers=admin_headers,
                               json={"type": "magiclink", "email": email})
        if link.is_error:
            raise SsoError("Không tạo được phiên đăng nhập (Supabase).")
        token_hash = link.json().get("hashed_token") or (link.json().get("properties") or {}).get("hashed_token")
        if not token_hash:
            raise SsoError("Không tạo được phiên đăng nhập (thiếu token).")
        verify = self._http.post(f"{self.base_url}/auth/v1/verify", headers={"apikey": self.anon_key},
                                 json={"type": "magiclink", "token_hash": token_hash})
        data = verify.json() if verify.content else {}
        if verify.is_error or not data.get("access_token") or not data.get("refresh_token"):
            raise SsoError("Không xác minh được phiên đăng nhập (Supabase).")
        return {"access_token": data["access_token"], "refresh_token": data["refresh_token"]}
