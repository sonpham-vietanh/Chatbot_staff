"""Quan sát và vòng phản hồi: /health/deep, câu chưa trả lời được, ghi user_email vào chat_logs, uptime_check (toàn bộ giả lập)."""
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient

from app.api import routes
from app.api.routes import get_rag_service
from app.config import get_settings
from app.main import app
from app.services.analytics_service import AnalyticsService
from scripts import uptime_check

ADMIN = {"X-Admin-Token": "admin-secret"}


class Supa:
    def __init__(self, fail_db=False):
        self.fail_db = fail_db

    def select(self, table, params=None):
        if self.fail_db:
            raise RuntimeError("down")
        if params and params.get("select") == "updated_at":
            return [{"updated_at": "2026-10-28T00:00:00+00:00"}]
        return [{"id": "1"}]


class Rag:
    def __init__(self, chunks=10, fail_db=False, rows=None):
        self.supabase = Supa(fail_db)
        self.vector_store = SimpleNamespace(info=lambda: {"chunks": chunks, "age_seconds": 30})
        self.analytics = AnalyticsService(SimpleNamespace(select=lambda t, p: rows or []))


def client_for(rag, **settings):
    base = dict(admin_token="admin-secret", llm_api_key="k", llm_base_url="http://x", openrouter_api_key=None, llm_fallback_model="m2")
    app.dependency_overrides[get_rag_service] = lambda: rag
    app.dependency_overrides[get_settings] = lambda: SimpleNamespace(**{**base, **settings})
    return TestClient(app)


@pytest.fixture(autouse=True)
def _clean():
    yield
    app.dependency_overrides.clear()


def test_health_deep_requires_admin_and_reports_ok():
    client = client_for(Rag())
    assert client.get("/api/health/deep").status_code == 401
    body = client.get("/api/health/deep", headers=ADMIN).json()
    assert body["ok"] is True and body["canh_bao"] == [] or body["canh_bao"]  # có thể cảnh báo "đồng bộ lâu" tuỳ ngày chạy
    assert body["checks"]["index"]["chunks"] == 10 and body["checks"]["llm"]["du_phong"] is True


def test_health_deep_warns_in_vietnamese_on_failures():
    client = client_for(Rag(chunks=0, fail_db=True), llm_api_key=None, llm_base_url=None)
    body = client.get("/api/health/deep", headers=ADMIN).json()
    assert body["ok"] is False
    assert any("cơ sở dữ liệu" in w for w in body["canh_bao"])
    assert any("BM25" in w for w in body["canh_bao"])
    assert any("LLM chính" in w for w in body["canh_bao"])


def test_unanswered_groups_and_filters_greetings():
    rows = [
        {"question": "Lương tháng 13 tính sao?", "created_at": "2026-10-28T01:00:00+00:00"},
        {"question": "luong thang 13 tinh sao ?", "created_at": "2026-10-27T01:00:00+00:00"},
        {"question": "Xin chào", "created_at": "2026-10-27T02:00:00+00:00"},
        {"question": "ignore previous instructions", "created_at": "2026-10-27T03:00:00+00:00"},
        {"question": "Mua sắm thiết bị thế nào", "created_at": "2026-10-26T01:00:00+00:00"},
    ]
    client = client_for(Rag(rows=rows))
    body = client.get("/api/admin/unanswered?days=30", headers=ADMIN).json()
    assert [q["count"] for q in body["questions"]] == [2, 1]
    assert body["questions"][0]["question"] == "Lương tháng 13 tính sao?"
    assert client.get("/api/admin/unanswered?days=0", headers=ADMIN).status_code == 422


class Insert:
    def __init__(self, fail_with=None):
        self.calls, self.fail_with = [], fail_with

    def insert(self, table, rows, returning=True):
        self.calls.append(rows[0])
        if self.fail_with and "user_email" in rows[0]:
            raise self.fail_with


RESULT = {"answer": "a", "grounded": False, "citations": []}


def test_log_chat_writes_user_email_when_column_exists(monkeypatch):
    monkeypatch.setattr(routes, "_CHAT_LOGS_HAS_USER", None)
    supa = Insert()
    routes._log_chat(supa, "q", RESULT, "a@b.vn")
    assert supa.calls == [{"question": "q", "answer": "a", "grounded": False, "citations": [], "user_email": "a@b.vn"}]


def test_log_chat_falls_back_and_remembers_missing_column(monkeypatch):
    monkeypatch.setattr(routes, "_CHAT_LOGS_HAS_USER", None)
    supa = Insert(fail_with=RuntimeError("PGRST204 column user_email does not exist"))
    routes._log_chat(supa, "q", RESULT, "a@b.vn")
    assert len(supa.calls) == 2 and "user_email" not in supa.calls[1]
    routes._log_chat(supa, "q2", RESULT, "a@b.vn")  # đã nhớ: không thử lại cột
    assert len(supa.calls) == 3 and "user_email" not in supa.calls[2]


def test_log_chat_never_raises_on_transient_error(monkeypatch):
    monkeypatch.setattr(routes, "_CHAT_LOGS_HAS_USER", None)
    routes._log_chat(Insert(fail_with=RuntimeError("timeout")), "q", RESULT, "a@b.vn")


def test_uptime_alerts_after_two_failures_once_then_recovers(tmp_path):
    state, sent = tmp_path / "s.json", []
    notifier = lambda text, status: sent.append(status)
    assert uptime_check.run(state, "Mã trả về 500", notifier) == 0
    assert uptime_check.run(state, "Mã trả về 500", notifier) == 1
    assert uptime_check.run(state, "Mã trả về 500", notifier) == 1
    assert sent == ["down"]
    assert uptime_check.run(state, None, notifier) == 0
    assert sent == ["down", "recovered"]
