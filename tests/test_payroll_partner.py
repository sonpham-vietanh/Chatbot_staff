"""Tích hợp app lương: chữ ký HMAC, bài hướng dẫn, sửa ngược hồ sơ (danh sách trắng), webhook danh bạ, bong bóng chat."""
import json
import time
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient

from app.api.partner_routes import get_payroll
from app.api.routes import _ASKER_CACHE, get_rag_service
from app.config import get_settings
from app.main import app
from app.services.directory_service import DirectoryService
from app.services.payroll_partner_service import (
    ACTOR, ARTICLE_DEPARTMENT, MAX_ARTICLES, PartnerError, PayrollPartnerService, SOURCE_PREFIX, sign,
)
from app.services.webhook_service import WebhookService
from tests.test_directory_admin import FakeClient as DirectoryFakeClient, make, person
from tests.test_heartbeat_webhook import FakePost

SECRET = "p" * 40
ADMIN = {"X-Admin-Token": "admin-secret"}


class Client(DirectoryFakeClient):
    """Thêm bảng tri thức + nhật ký chat, bộ lọc like. và delete cho FakeClient của danh bạ."""

    def __init__(self):
        super().__init__()
        self.tables["knowledge_notes"] = []
        self.tables["chat_logs"] = []

    @staticmethod
    def _match(row, params):
        rest = {}
        for key, value in params.items():
            if value.startswith("like."):
                if not str(row.get(key) or "").startswith(value[5:].rstrip("*")):
                    return False
            else:
                rest[key] = value
        return DirectoryFakeClient._match(row, rest)

    def delete(self, table, params):
        self.tables[table] = [r for r in self.tables[table] if not self._match(r, params)]


class FakeAdmin:
    def __init__(self, client):
        self.client = client

    def create_note(self, title, department, content, access_level="staff", status="draft", created_by="Admin", source_file=None):
        return self.client.insert("knowledge_notes", [{"title": title, "department": department, "content": content, "access_level": access_level,
                                                       "status": status, "created_by": created_by, "source_file": source_file}])[0]

    def save_note(self, note_id, patch):
        return self.client.update("knowledge_notes", {"id": f"eq.{note_id}"}, patch)[0]

    def delete_note(self, note_id):
        self.client.delete("knowledge_notes", {"id": f"eq.{note_id}"})


def build(webhook_codes=(200,), secret=SECRET):
    directory, requests, client = make()
    client.tables["knowledge_notes"] = []
    client.tables["chat_logs"] = []
    client.__class__ = Client
    post = FakePost(*webhook_codes)
    webhook = WebhookService("https://luong.example/hook", "w" * 40, sleep=lambda _s: None, post=post, run_inline=True)
    payroll = PayrollPartnerService(client, FakeAdmin(client), directory, webhook, secret)
    directory.on_change = payroll.push_employees
    return SimpleNamespace(directory=directory, requests=requests, client=client, post=post, webhook=webhook, payroll=payroll)


def headers(method, path, body=b"", secret=SECRET, ts=None):
    stamp = str(int(time.time()) if ts is None else ts)
    return {"X-Timestamp": stamp, "X-Signature": sign(secret, stamp, method, path, body), "Content-Type": "application/json"}


def sent(post):
    return [json.loads(call.body) for call in post.calls]


# ---------- chữ ký ----------
def test_verify_accepts_valid_and_rejects_tampering_replay_and_cross_endpoint():
    payroll = build().payroll
    body = b'{"a":1}'
    ts = str(int(time.time()))
    good = sign(SECRET, ts, "POST", "/api/partner/payroll/chat", body)
    payroll.verify(ts, good, "POST", "/api/partner/payroll/chat", body)
    for args in (
        (ts, good, "POST", "/api/partner/payroll/chat", b'{"a":2}'),       # sửa body
        (ts, good, "POST", "/api/partner/payroll/knowledge", body),        # dùng chéo endpoint
        (ts, good, "PUT", "/api/partner/payroll/chat", body),              # đổi phương thức
        (ts, sign("x" * 40, ts, "POST", "/api/partner/payroll/chat", body), "POST", "/api/partner/payroll/chat", body),  # sai khoá
        (ts, "", "POST", "/api/partner/payroll/chat", body),
        (None, good, "POST", "/api/partner/payroll/chat", body),
    ):
        with pytest.raises(PartnerError) as error:
            payroll.verify(*args)
        assert error.value.status_code == 401
    stale = str(int(time.time()) - 600)
    with pytest.raises(PartnerError) as error:
        payroll.verify(stale, sign(SECRET, stale, "POST", "/p", body), "POST", "/p", body)
    assert error.value.status_code == 401


@pytest.mark.parametrize("secret", [None, "", "ngan"])
def test_inbound_disabled_without_a_strong_secret(secret):
    payroll = build(secret=secret).payroll
    assert not payroll.enabled
    with pytest.raises(PartnerError) as error:
        payroll.verify("1", "x", "GET", "/p", b"")
    assert error.value.status_code == 503


# ---------- bài hướng dẫn ----------
def test_push_articles_creates_updates_skips_unchanged_and_deletes():
    env = build()
    article = {"id": "cach-xem-phieu-luong", "title": "Cách xem phiếu lương", "content": "Vào mục Phiếu lương rồi chọn tháng."}
    first = env.payroll.push_articles([article], [])
    assert (first["created"], first["updated"], first["unchanged"]) == (1, 0, 0)
    note = env.client.tables["knowledge_notes"][0]
    assert (note["source_file"], note["department"], note["status"], note["created_by"]) == (SOURCE_PREFIX + "cach-xem-phieu-luong", ARTICLE_DEPARTMENT, "approved", "PayrollApp")
    assert env.payroll.push_articles([article], [])["unchanged"] == 1
    changed = env.payroll.push_articles([{**article, "content": "Nội dung mới."}], [])
    assert changed["updated"] == 1 and env.client.tables["knowledge_notes"][0]["content"] == "Nội dung mới."
    assert len(env.client.tables["knowledge_notes"]) == 1
    deleted = env.payroll.push_articles([], ["cach-xem-phieu-luong", "khong-ton-tai"])
    assert deleted["deleted"] == 1 and env.client.tables["knowledge_notes"] == []


def test_push_articles_never_touches_notes_it_does_not_own_and_rejects_oversized_batch():
    env = build()
    env.client.tables["knowledge_notes"] = [{"id": "n1", "title": "Quy định nghỉ phép", "content": "x", "department": "HR", "status": "approved", "source_file": "vault:nhan-su/x.md"}]
    env.payroll.push_articles([], ["x.md", "nhan-su/x.md"])
    assert len(env.client.tables["knowledge_notes"]) == 1
    with pytest.raises(PartnerError) as error:
        env.payroll.push_articles([{"id": f"a{i}", "title": "t", "content": "c"} for i in range(MAX_ARTICLES + 1)], [])
    assert error.value.status_code == 422
    dup = env.payroll.push_articles([{"id": "a", "title": "t", "content": "c"}, {"id": "a", "title": "t2", "content": "c2"}], [])
    assert dup["created"] == 1 and dup["errors"][0]["id"] == "a"


# ---------- sửa ngược hồ sơ ----------
def test_update_employees_only_whitelisted_fields_audited_and_not_echoed_back():
    env = build()
    result = env.payroll.update_employees([
        {"email": "lan@truongvietanh.com", "employment_start_date": "2023-05-01", "contract_type": "HĐLĐ 12 tháng",
         "display_name": "Bị Bỏ Qua", "job_title": "Giám đốc", "department": "Khác"},
        {"email": "mgr@truongvietanh.com", "level_name": "Bậc 3"},
        {"email": "lan@truongvietanh.com", "contract_type": "HĐLĐ 12 tháng"},
    ])
    assert result["updated"] == 2 and result["unchanged"] == 1 and result["errors"] == []
    lan = env.directory.get("lan@truongvietanh.com")
    assert (lan["employment_start_date"], lan["contract_type"]) == ("2023-05-01", "HĐLĐ 12 tháng")
    assert (lan["display_name"], lan["job_title"], lan["department"]) == ("Nguyễn Thị Lan", "Chuyên viên", "HR")
    assert {r["actor"] for r in env.client.tables["profile_audit_log"]} == {ACTOR}
    assert env.post.calls == []  # sửa từ app lương không đẩy ngược lại app lương


def test_update_employees_reports_unknown_people_empty_updates_and_bad_values():
    env = build()
    result = env.payroll.update_employees([
        {"email": "ghost@truongvietanh.com", "level_name": "Bậc 1"},
        {"email": "lan@truongvietanh.com", "display_name": "Chỉ trường cấm"},
        {"email": "lan@truongvietanh.com", "employment_start_date": "01/05/2023"},
    ])
    assert result["updated"] == 0 and [e["row"] for e in result["errors"]] == [1, 2, 3]
    assert "Chưa có trong danh bạ" in result["errors"][0]["message"]


# ---------- bot -> app lương ----------
def test_every_directory_change_is_pushed_with_only_allowed_fields():
    env = build()
    env.directory.create({"email": "moi@truongvietanh.com", "display_name": "Người Mới", "department": "SO", "job_title": "NV", "work_phone": "0901234567"})
    env.directory.update("lan@truongvietanh.com", {"job_title": "Trưởng nhóm"})
    env.directory.deactivate("mgr@truongvietanh.com", "2026-11-30")
    env.directory.reactivate("off@truongvietanh.com")
    env.directory.import_rows([{"email": "lan@truongvietanh.com", "campus": "Cơ sở 2"}, {"email": "nhap@truongvietanh.com", "display_name": "Nhập", "department": "FI", "job_title": "KT"}], dry_run=False)
    env.directory.import_rows([{"email": "lan@truongvietanh.com", "campus": "Cơ sở 3"}], dry_run=True)
    payloads = sent(env.post)
    assert len(payloads) == 5
    for payload in payloads:
        assert payload["su_kien"]["tinh_nang"] == "nhan_vien_cap_nhat"
        for employee in payload["su_kien"]["chi_tiet"]["nhan_vien"]:
            assert "work_phone" not in employee and "subjects" not in employee and "email" in employee
    assert payloads[0]["su_kien"]["chi_tiet"]["nhan_vien"][0]["email"] == "moi@truongvietanh.com"
    assert payloads[2]["su_kien"]["chi_tiet"]["nhan_vien"][0]["active"] is False
    assert sorted(e["email"] for e in payloads[4]["su_kien"]["chi_tiet"]["nhan_vien"]) == ["lan@truongvietanh.com", "nhap@truongvietanh.com"]


def test_approving_a_change_request_pushes_the_new_value():
    env = build()
    created = env.requests.create("lan@truongvietanh.com", "job_title", "Trưởng nhóm", "Đã được bổ nhiệm")
    env.post.calls.clear()
    env.requests.approve(created["id"])
    employee = sent(env.post)[0]["su_kien"]["chi_tiet"]["nhan_vien"][0]
    assert employee["email"] == "lan@truongvietanh.com" and employee["job_title"] == "Trưởng nhóm"


def test_large_import_is_batched_and_webhook_failure_never_breaks_hr():
    env = build(webhook_codes=(500,))
    rows = [person(f"u{i}@truongvietanh.com", f"U{i}") for i in range(250)]
    env.payroll.push_employees(rows)
    assert len(env.post.calls) // 3 == 3  # 250 người -> 3 webhook (100+100+50), mỗi cái thử lại 3 lần khi 5xx
    env.directory.update("lan@truongvietanh.com", {"job_title": "NV cao cấp"})  # không ném lỗi dù người nhận đang hỏng
    assert env.directory.get("lan@truongvietanh.com")["job_title"] == "NV cao cấp"


def test_no_webhook_when_not_configured():
    env = build()
    env.payroll.webhook = WebhookService(None, None)
    env.directory.update("lan@truongvietanh.com", {"job_title": "Khác"})
    assert env.post.calls == []


def test_snapshot_pages_through_everyone_including_inactive():
    env = build()
    page = env.payroll.snapshot(0, 2)
    assert page["tong"] == 3 and len(page["nhan_vien"]) == 2
    assert {e["email"] for e in env.payroll.snapshot(2, 2)["nhan_vien"]} <= {"off@truongvietanh.com", "lan@truongvietanh.com", "mgr@truongvietanh.com"}
    assert all("work_phone" not in e for e in page["nhan_vien"])


# ---------- HTTP ----------
@pytest.fixture
def api():
    env = build()
    chats = []

    def chat(question, history, asker_department=None, **extra):
        chats.append(SimpleNamespace(question=question, department=asker_department, extra=extra))
        return {"answer": "**Xem phiếu lương** ở mục Phiếu lương.", "grounded": True, "citations": []}

    rag = SimpleNamespace(payroll=env.payroll, directory=env.directory, supabase=env.client, chat=chat, payroll_webhook=env.webhook)
    app.dependency_overrides[get_rag_service] = lambda: rag
    app.dependency_overrides[get_settings] = lambda: SimpleNamespace(admin_token="admin-secret", public_base_url="x")
    _ASKER_CACHE.clear()
    try:
        yield SimpleNamespace(http=TestClient(app), env=env, chats=chats)
    finally:
        app.dependency_overrides.clear()
        _ASKER_CACHE.clear()


def call(api, method, path, payload=None, **kwargs):
    body = json.dumps(payload, ensure_ascii=False).encode() if payload is not None else b""
    return api.http.request(method, path, content=body, headers=headers(method, path, body), **kwargs)


def test_http_rejects_unsigned_badly_signed_and_unconfigured_requests(api):
    assert api.http.get("/api/partner/payroll/ping").status_code == 401
    bad = api.http.get("/api/partner/payroll/ping", headers=headers("GET", "/api/partner/payroll/ping", secret="z" * 40))
    assert bad.status_code == 401
    assert call(api, "GET", "/api/partner/payroll/ping").json()["ok"] is True
    api.env.payroll.secret = None
    assert call(api, "GET", "/api/partner/payroll/ping").status_code == 503


def test_http_chat_answers_as_the_signed_employee_in_plain_text(api):
    response = call(api, "POST", "/api/partner/payroll/chat", {"email": "LAN@truongvietanh.com", "question": "Xem phiếu lương ở đâu?"})
    assert response.status_code == 200
    data = response.json()
    assert data["grounded"] is True and "**" not in data["answer"] and "Phiếu lương" in data["answer"]
    assert data["answer_markdown"].startswith("**Xem phiếu lương**")  # bản giữ định dạng cho bong bóng
    assert api.chats[0].department == "HR" and "Nguyễn Thị Lan" in api.chats[0].extra["asker"]
    assert api.env.client.tables["chat_logs"][0]["user_email"] == "lan@truongvietanh.com"


def test_http_chat_refuses_unknown_or_departed_employees_and_validates_input(api):
    for email in ("ghost@truongvietanh.com", "off@truongvietanh.com"):
        assert call(api, "POST", "/api/partner/payroll/chat", {"email": email, "question": "Xin chào bạn"}).status_code == 403
    assert call(api, "POST", "/api/partner/payroll/chat", {"email": "khong-phai-email", "question": "Xin chào bạn"}).status_code == 422
    assert call(api, "POST", "/api/partner/payroll/chat", {"email": "lan@truongvietanh.com", "question": " "}).status_code == 422
    assert api.chats == []


def test_http_chat_is_rate_limited_per_employee(api):
    payload = {"email": "lan@truongvietanh.com", "question": "Xem phiếu lương ở đâu?"}
    codes = [call(api, "POST", "/api/partner/payroll/chat", payload).status_code for _ in range(22)]
    assert codes.count(200) == 20 and codes[-1] == 429
    other = call(api, "POST", "/api/partner/payroll/chat", {**payload, "email": "mgr@truongvietanh.com"})
    assert other.status_code == 200


def test_http_knowledge_and_employees_and_snapshot(api):
    pushed = call(api, "PUT", "/api/partner/payroll/knowledge", {"articles": [{"id": "huong-dan-1", "title": "Hướng dẫn", "content": "Nội dung"}]})
    assert pushed.status_code == 200 and pushed.json()["created"] == 1
    assert call(api, "PUT", "/api/partner/payroll/knowledge", {"articles": [{"id": "bad id!", "title": "t", "content": "c"}]}).status_code == 422
    assert call(api, "PUT", "/api/partner/payroll/knowledge", {"articles": [{"id": "ok", "title": "t", "content": "c", "luong": 1}]}).status_code == 422
    assert call(api, "PUT", "/api/partner/payroll/knowledge", {"delete_ids": ["../x"]}).status_code == 422
    updated = call(api, "POST", "/api/partner/payroll/employees", {"employees": [{"email": "lan@truongvietanh.com", "level_name": "Bậc 2"}]})
    assert updated.json()["updated"] == 1
    snapshot = call(api, "GET", "/api/partner/payroll/directory", params={"offset": 0, "limit": 2})
    assert snapshot.status_code == 200 and snapshot.json()["tong"] == 3 and len(snapshot.json()["nhan_vien"]) == 2


def test_admin_can_test_the_outbound_webhook(api):
    assert api.http.post("/api/admin/payroll-webhook/test").status_code in (401, 403, 503)
    ok = api.http.post("/api/admin/payroll-webhook/test", headers=ADMIN)
    assert ok.status_code == 200 and ok.json() == {"ok": True, "ma_phan_hoi": 200, "ghi_chu": None}
    body = sent(api.env.post)[-1]
    assert body["su_kien"]["tinh_nang"] == "thu_ket_noi"
    api.env.webhook.url = None
    assert api.http.post("/api/admin/payroll-webhook/test", headers=ADMIN).status_code == 409
