"""Kiểm tra qua đúng tầng HTTP của FastAPI (TestClient, không chạy lifespan nên VaultWatcher
không khởi động) với service giả trong bộ nhớ — không gọi mạng."""
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient

from app.api.routes import get_rag_service
from app.config import get_settings
from app.main import app
from app.services.api_key_service import ApiKeyService
from tests.test_api_key_service import FakeClient

ADMIN = {"X-Admin-Token": "admin-secret"}
MARKDOWN_ANSWER = (
    "## Nghỉ phép\n"
    "Bạn cần báo trước **48 giờ** qua email `hr@truongvietanh.com`.\n"
    "* **Leaders**: xin phép CEO\n"
    "* Nhân viên: xin phép quản lý trực tiếp"
)
CITATIONS = [{"source": "Quy định nghỉ phép", "heading": "Quy định > Quy trình", "version": "0.1"}]


class FakeRag:
    def __init__(self):
        self.api_keys = ApiKeyService(FakeClient())
        self.supabase = SimpleNamespace(insert=lambda *args, **kwargs: None)
        self.chat_error: Exception | None = None

    def chat(self, question, history=None, asker_department=None):
        if self.chat_error:
            raise self.chat_error
        return {"answer": MARKDOWN_ANSWER, "grounded": True, "citations": list(CITATIONS)}


@pytest.fixture
def api():
    rag = FakeRag()
    app.dependency_overrides[get_rag_service] = lambda: rag
    app.dependency_overrides[get_settings] = lambda: SimpleNamespace(admin_token="admin-secret")
    try:
        yield TestClient(app), rag
    finally:
        app.dependency_overrides.clear()


def create_key(client, origin="https://os.truongvietanh.com"):
    response = client.post("/api/admin/api-keys", headers=ADMIN, json={"label": "Major OS", "allowed_origin": origin})
    assert response.status_code == 200, response.text
    return response.json()


def test_widget_chat_returns_plain_text_without_markdown_markers(api):
    client, _ = api
    key = create_key(client)

    response = client.post("/api/widget/chat", headers={"X-Widget-Key": key["key"]}, json={"question": "Nghỉ phép?"})

    assert response.status_code == 200
    body = response.json()
    assert body["answer"] == (
        "Nghỉ phép\n"
        "Bạn cần báo trước 48 giờ qua email hr@truongvietanh.com.\n\n"
        "- Leaders: xin phép CEO\n"
        "- Nhân viên: xin phép quản lý trực tiếp"
    )
    assert body["citations"] == CITATIONS
    assert body["grounded"] is True
    assert body["thread_id"] is None


def test_widget_chat_auth_and_validation_errors(api):
    client, rag = api
    key = create_key(client)
    headers = {"X-Widget-Key": key["key"]}

    assert client.post("/api/widget/chat", json={"question": "Nghỉ phép?"}).status_code == 401
    assert client.post("/api/widget/chat", headers={"X-Widget-Key": "vas_sai"}, json={"question": "Nghỉ phép?"}).status_code == 401
    assert client.post("/api/widget/chat", headers={**headers, "Origin": "https://evil.example.com"},
                       json={"question": "Nghỉ phép?"}).status_code == 403
    assert client.post("/api/widget/chat", headers=headers, json={"question": "   "}).status_code == 422
    assert client.post("/api/widget/chat", headers=headers,
                       json={"question": "Nghỉ phép?", "history": [{"role": "bot", "content": "x"}]}).status_code == 422

    rag.chat_error = RuntimeError("OpenRouter HTTP 500")
    failed = client.post("/api/widget/chat", headers=headers, json={"question": "Nghỉ phép?"})
    assert failed.status_code == 503  # không dùng 502: Cloudflare thay nó bằng trang HTML
    assert "OpenRouter" not in failed.text  # không lộ chi tiết lỗi nội bộ cho bên tích hợp


def test_database_outage_during_key_lookup_is_a_json_503_not_a_bare_500(api):
    client, rag = api

    def broken_lookup(key):
        raise RuntimeError("supabase down")

    rag.api_keys.get_active_key = broken_lookup
    response = client.post("/api/widget/chat", headers={"X-Widget-Key": "vas_x"}, json={"question": "Nghỉ phép?"})

    assert response.status_code == 503
    assert "detail" in response.json()


def test_admin_api_key_routes_reject_bad_input_with_4xx_not_500(api):
    client, _ = api

    assert client.get("/api/admin/api-keys").status_code == 401
    # Token sai có ký tự ngoài ASCII từng làm compare_digest ném TypeError -> 500.
    assert client.get("/api/admin/api-keys", headers={"X-Admin-Token": "tok\xe9n".encode("latin-1")}).status_code == 401

    for origin in ("https://a.com *", "https://a.com;script-src *", "https://*.truongvietanh.com",
                   "https://os.truongvietanh.com/app", "os.truongvietanh.com"):
        response = client.post("/api/admin/api-keys", headers=ADMIN, json={"label": "x", "allowed_origin": origin})
        assert response.status_code == 422, origin
    blank_label = client.post("/api/admin/api-keys", headers=ADMIN,
                              json={"label": "   ", "allowed_origin": "https://os.truongvietanh.com"})
    assert blank_label.status_code == 422

    assert client.post("/api/admin/api-keys/not-a-uuid/revoke", headers=ADMIN).status_code == 422
    assert client.delete("/api/admin/api-keys/not-a-uuid", headers=ADMIN).status_code == 422
    assert client.post("/api/admin/api-keys/00000000-0000-0000-0000-000000000000/revoke", headers=ADMIN).status_code == 404
