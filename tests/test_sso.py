"""Đăng nhập 1 lần từ Major OS: chỉ chấp nhận mã HS256 hợp lệ, còn hạn, đúng domain, dùng 1 lần."""
import time
import uuid
from urllib.parse import parse_qs, urlparse

import pytest
from fastapi.testclient import TestClient

import app.main as main
from app.services.sso_service import SsoError, SsoService, sign_token

SECRET = "s" * 40


def make_service():
    return SsoService("https://x.supabase.co", "service", "anon", SECRET, "truongvietanh.com")


def claims(**overrides):
    now = int(time.time())
    base = {"iss": "major-os", "email": "GV@TruongVietAnh.com", "iat": now, "exp": now + 120, "jti": uuid.uuid4().hex}
    return {**base, **overrides}


def test_valid_token_returns_normalized_email_once():
    sso = make_service()
    token = sign_token(claims(), SECRET)

    assert sso.verify_token(token) == "gv@truongvietanh.com"
    with pytest.raises(SsoError, match="đã được dùng"):
        sso.verify_token(token)


@pytest.mark.parametrize("bad, message", [
    (lambda: sign_token(claims(), "x" * 40), "Chữ ký"),
    (lambda: sign_token(claims(exp=int(time.time()) - 600, iat=int(time.time()) - 700), SECRET), "hết hạn"),
    (lambda: sign_token(claims(exp=int(time.time()) + 3600), SECRET), "Thời hạn"),
    (lambda: sign_token(claims(email="a@gmail.com"), SECRET), "domain"),
    (lambda: sign_token(claims(iss="other"), SECRET), "Nguồn"),
    (lambda: sign_token(claims(jti="short"), SECRET), "jti"),
    (lambda: "not.a.jwt", "định dạng"),
])
def test_invalid_tokens_are_rejected(bad, message):
    with pytest.raises(SsoError, match=message):
        make_service().verify_token(bad())


def test_disabled_without_long_secret():
    sso = SsoService("https://x.supabase.co", "service", "anon", "short", "truongvietanh.com")
    with pytest.raises(SsoError, match="chưa được cấu hình"):
        sso.verify_token(sign_token(claims(), "short"))


def test_route_hands_session_to_browser_in_fragment_only(monkeypatch):
    sso = make_service()
    sso.create_session = lambda email: {"access_token": f"acc-{email}", "refresh_token": "ref"}
    monkeypatch.setattr(main, "get_sso_service", lambda: sso)
    client = TestClient(main.app)

    ok = client.get(f"/sso/os?token={sign_token(claims(), SECRET)}", follow_redirects=False)
    assert ok.status_code == 303
    location = urlparse(ok.headers["location"])
    assert location.path == "/" and not location.query            # không lộ token trên query
    assert parse_qs(location.fragment)["sso_access"] == ["acc-gv@truongvietanh.com"]
    assert ok.headers["cache-control"] == "no-store"

    bad = client.get("/sso/os?token=abc", follow_redirects=False)
    assert bad.status_code == 303 and "sso_error=" in bad.headers["location"] and "#" not in bad.headers["location"]


def test_token_with_non_object_header_or_claims_is_rejected_cleanly():
    import base64, hashlib, hmac, json
    service = make_service()

    def b64(raw):
        return base64.urlsafe_b64encode(raw).rstrip(b"=").decode()

    for header, claims in (([1], {}), ({"alg": "HS256"}, ["x"]), ("str", {"a": 1})):
        h, c = b64(json.dumps(header).encode()), b64(json.dumps(claims).encode())
        sig = b64(hmac.new(SECRET.encode(), f"{h}.{c}".encode(), hashlib.sha256).digest())
        with pytest.raises(SsoError):
            service.verify_token(f"{h}.{c}.{sig}")
