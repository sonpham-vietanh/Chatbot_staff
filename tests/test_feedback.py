"""Nút "Báo sai": nhân viên đã đăng nhập gửi báo cáo, chỉ admin xem/xử lý được."""
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient

from app.api.routes import get_rag_service, require_user
from app.config import get_settings
from app.main import app
from app.services.feedback_service import FeedbackService

ADMIN = {"X-Admin-Token": "admin-secret"}
REPORT = {
    "question": "Tôi cần xin nghỉ phép trước bao lâu?",
    "answer": "Bạn cần báo trước **48 giờ**.",
    "citations": [{"source": "Quy định nghỉ phép", "heading": "Quy trình", "version": "0.1"}],
    "reason": "wrong_info",
    "note": "  Quy định mới là 3 ngày  ",
}


class FakeClient:
    def __init__(self):
        self.rows = []

    def insert(self, table, rows, returning=True):
        for index, row in enumerate(rows):
            self.rows.append({**row, "id": f"00000000-0000-0000-0000-00000000000{len(self.rows) + 1}",
                              "status": "open", "created_at": "2026-10-04T00:00:00Z", "handled_at": None})
        return self.rows[-len(rows):]

    def select(self, table, params):
        rows = self.rows
        if "status" in params:
            rows = [r for r in rows if r["status"] == params["status"].removeprefix("eq.")]
        return list(rows)

    def update(self, table, params, patch):
        matched = [r for r in self.rows if r["id"] == params["id"].removeprefix("eq.")]
        for row in matched:
            row.update(patch)
        return matched


@pytest.fixture
def client():
    fake = FakeClient()
    rag = SimpleNamespace(feedback=FeedbackService(fake), auth=None, supabase=None)
    app.dependency_overrides[get_rag_service] = lambda: rag
    app.dependency_overrides[get_settings] = lambda: SimpleNamespace(admin_token="admin-secret")
    app.dependency_overrides[require_user] = lambda: {"id": "11111111-1111-1111-1111-111111111111", "email": "gv@truongvietanh.com"}
    try:
        yield TestClient(app), fake
    finally:
        app.dependency_overrides.clear()


def test_staff_report_is_stored_with_reporter_and_full_context(client):
    http, fake = client

    response = http.post("/api/feedback", json=REPORT)

    assert response.status_code == 200
    stored = fake.rows[0]
    assert stored["user_email"] == "gv@truongvietanh.com"
    assert stored["question"] == REPORT["question"] and stored["answer"] == REPORT["answer"]
    assert stored["citations"] == REPORT["citations"]
    assert stored["note"] == "Quy định mới là 3 ngày"


def test_report_requires_login_and_valid_reason(client):
    http, _ = client
    assert http.post("/api/feedback", json={**REPORT, "reason": "spam"}).status_code == 422
    assert http.post("/api/feedback", json={**REPORT, "answer": "   "}).status_code == 422

    app.dependency_overrides.pop(require_user)
    assert http.post("/api/feedback", json=REPORT).status_code == 401


def test_admin_lists_and_resolves_reports(client):
    http, _ = client
    http.post("/api/feedback", json=REPORT)

    assert http.get("/api/admin/feedback").status_code == 401
    listing = http.get("/api/admin/feedback", headers=ADMIN).json()
    assert listing["open_count"] == 1 and len(listing["items"]) == 1
    feedback_id = listing["items"][0]["id"]

    done = http.post(f"/api/admin/feedback/{feedback_id}/status", headers=ADMIN, json={"status": "resolved"})
    assert done.status_code == 200
    after = http.get("/api/admin/feedback?status=resolved", headers=ADMIN).json()
    assert after["open_count"] == 0 and after["items"][0]["handled_at"]

    assert http.post("/api/admin/feedback/not-a-uuid/status", headers=ADMIN, json={"status": "resolved"}).status_code == 422
    missing = "00000000-0000-0000-0000-000000000099"
    assert http.post(f"/api/admin/feedback/{missing}/status", headers=ADMIN, json={"status": "resolved"}).status_code == 404
