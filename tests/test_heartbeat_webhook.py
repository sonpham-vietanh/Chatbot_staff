"""Phút sử dụng (heartbeat) + webhook sang Major OS: đếm, chống đếm quá, chữ ký, thử lại, ngắt mạch, tắt khi thiếu cấu hình."""
import hashlib
import hmac
import json
import threading
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient

from app.api import routes
from app.api.routes import get_rag_service, require_user
from app.config import get_settings
from app.main import app
from app.services import webhook_service
from app.services.report_service import ReportKeyService, ReportService
from app.services.usage_service import FEATURES, UsageService
from app.services.webhook_service import WebhookService, sign
from tests.test_report import ADMIN, FakeClient, make_event

USER = {"id": "u1", "email": "lan@truongvietanh.com", "user_metadata": {"full_name": "Lan"}}


class FakePost:
    def __init__(self, *codes):
        self.codes = list(codes)
        self.calls = []

    def __call__(self, url, content=None, headers=None, timeout=None):
        self.calls.append(SimpleNamespace(url=url, body=content, headers=headers, timeout=timeout))
        code = self.codes.pop(0) if len(self.codes) > 1 else self.codes[0]
        if isinstance(code, Exception):
            raise code
        return SimpleNamespace(status_code=code)


def make_webhook(*codes, url="https://os.example/hook", secret="s3cret"):
    sleeps = []
    post = FakePost(*codes)
    return WebhookService(url, secret, sleep=sleeps.append, post=post, run_inline=True), post, sleeps


@pytest.fixture
def env():
    client = FakeClient()
    webhook, post, sleeps = make_webhook(200)
    rag = SimpleNamespace(usage=UsageService(client, webhook), report=ReportService(client), report_keys=ReportKeyService(client),
                          supabase=client, webhook=webhook, auth=None)
    app.dependency_overrides[get_rag_service] = lambda: rag
    app.dependency_overrides[get_settings] = lambda: SimpleNamespace(admin_token="admin-secret", public_base_url="x")
    routes._HEARTBEATS.clear()
    http = TestClient(app)
    try:
        yield SimpleNamespace(http=http, client=client, post=post, webhook=webhook, rag=rag)
    finally:
        app.dependency_overrides.clear()
        routes._HEARTBEATS.clear()


def login(user=USER):
    app.dependency_overrides[require_user] = lambda: user


# ---------- heartbeat ----------
def test_heartbeat_requires_login(env):
    app.dependency_overrides.pop(require_user, None)
    assert env.http.post("/api/usage/heartbeat").status_code == 401


def test_heartbeat_counts_and_blocks_duplicates_within_50s(env):
    login()
    assert env.http.post("/api/usage/heartbeat", json={"session_id": "abc"}).json() == {"counted": True}
    assert env.http.post("/api/usage/heartbeat").json() == {"counted": False}
    rows = env.client.tables["usage_events"]
    assert len(rows) == 1 and rows[0]["feature"] == "phut_su_dung" and rows[0]["quantity"] == 1
    # quá 50 giây thì tính tiếp; người khác không bị ảnh hưởng
    routes._HEARTBEATS["u1"][0] -= 51
    assert env.http.post("/api/usage/heartbeat").json() == {"counted": True}
    login({**USER, "id": "u2", "email": "b@truongvietanh.com"})
    assert env.http.post("/api/usage/heartbeat").json() == {"counted": True}
    assert len(env.client.tables["usage_events"]) == 3


def test_heartbeat_daily_cap(env):
    login()
    today = datetime.now(timezone(timedelta(hours=7))).strftime("%Y-%m-%d")
    routes._HEARTBEATS["u1"] = [-1000.0, today, routes.HEARTBEAT_DAILY_CAP]
    assert env.http.post("/api/usage/heartbeat").json() == {"counted": False}
    routes._HEARTBEATS["u1"][1] = "2000-01-01"  # sang ngày mới thì đếm lại
    assert env.http.post("/api/usage/heartbeat").json() == {"counted": True}


def test_heartbeat_cache_is_bounded(monkeypatch):
    routes._HEARTBEATS.clear()
    monkeypatch.setattr(routes, "HEARTBEAT_MAX_ENTRIES", 5)
    for i in range(20):
        assert routes._heartbeat_counts(f"user{i}")
    assert len(routes._HEARTBEATS) <= 5
    routes._HEARTBEATS.clear()


# ---------- báo cáo ----------
def test_feature_listed_and_events_filterable(env):
    assert "phut_su_dung" in FEATURES
    env.client.tables["usage_events"] += [make_event(1, 0, feature="phut_su_dung"), make_event(2, 1, feature="hoi_dap")]
    report = {"Authorization": f"Bearer {env.rag.report_keys.create('Major OS')['key']}"}
    rows = env.http.get("/api/report/su-kien?tinh_nang=phut_su_dung", headers=report).json()["du_lieu"]
    assert [r["tinh_nang"] for r in rows] == ["phut_su_dung"]


def test_summary_adds_minutes_and_keeps_old_keys(env):
    now = datetime.now(timezone.utc).isoformat()
    events = [{**make_event(i, 0, feature="phut_su_dung"), "occurred_at": now, "meta": {}} for i in range(3)]
    events.append({**make_event(10, 0, email="bao@truongvietanh.com", feature="phut_su_dung"), "occurred_at": now, "meta": {}})
    events.append({**make_event(11, 0), "occurred_at": now})
    env.client.tables["usage_events"] += events
    result = env.rag.usage.summary(tu=None, den=None)
    for key in ("tong_so_lan", "so_nguoi_dung", "ty_le_tra_loi_duoc", "theo_tinh_nang", "nguoi_dung_nhieu_nhat", "bi_cat_bot"):
        assert key in result
    assert result["tong_so_lan"] == 1 and result["so_nguoi_dung"] == 1  # phút không làm đổi nghĩa lượt dùng
    assert result["nguoi_dung_nhieu_nhat"] == [{"email": "lan@truongvietanh.com", "ten": "Nguyễn Thị Lan", "so_lan": 1}]
    minutes = result["phut_su_dung"]
    assert minutes["tong_phut"] == 4 and minutes["so_nguoi_co_hoat_dong"] == 2 and minutes["phut_trung_binh_moi_nguoi"] == 2.0
    assert minutes["nguoi_dung_nhieu_nhat"][0] == {"email": "lan@truongvietanh.com", "ten": "Nguyễn Thị Lan", "so_phut": 3}
    assert sum(d["so_phut"] for d in minutes["theo_ngay"]) == 4
    assert next(f for f in result["theo_tinh_nang"] if f["tinh_nang"] == "phut_su_dung")["so_lan"] == 4


def test_admin_minutes_endpoint(env):
    assert env.http.get("/api/admin/usage-minutes").status_code == 401
    body = env.http.get("/api/admin/usage-minutes?days=7", headers=ADMIN).json()
    assert body["tong_phut"] == 0 and body["theo_ngay"] == []


# ---------- webhook ----------
def test_signature_and_headers_and_payload(env):
    login()
    env.http.post("/api/usage/heartbeat")
    call = env.post.calls[0]
    assert call.headers["X-Signature"] == "sha256=" + hmac.new(b"s3cret", call.body, hashlib.sha256).hexdigest()
    assert call.headers["Content-Type"] == "application/json" and call.timeout == 5.0
    payload = json.loads(call.body)
    event = payload["su_kien"]
    assert call.headers["X-Event-Id"] == event["id"]
    assert event["tinh_nang"] == "phut_su_dung" and event["so_luong"] == 1
    assert event["nguoi_dung"] == {"email": "lan@truongvietanh.com", "ten": "Lan"}
    assert event["thoi_diem"].endswith("+07:00") and event["chi_tiet"] == {}
    assert payload["ten_app"] == "Trợ lý nội bộ Trường Việt Anh" and payload["gio_may_chu"]


def test_only_exported_meta_in_webhook(env):
    env.rag.usage.log("hoi_dap", USER, {"tra_loi_duoc": True, "cau_hoi": "bí mật", "so_nguon": 2})
    assert json.loads(env.post.calls[0].body)["su_kien"]["chi_tiet"] == {"tra_loi_duoc": True, "so_nguon": 2}


def test_heartbeat_webhook_is_coalesced(env):
    for _ in range(4):
        env.rag.usage.log("phut_su_dung", USER)
    assert len(env.post.calls) == 1  # phút đầu gửi ngay, các phút sau dồn lại
    assert len(env.client.tables["usage_events"]) == 4
    env.webhook._coalesce["lan@truongvietanh.com"][0] -= 301
    env.rag.usage.log("phut_su_dung", USER)
    assert json.loads(env.post.calls[1].body)["su_kien"]["so_luong"] == 4


def test_retry_on_5xx_and_network_error():
    webhook, post, sleeps = make_webhook(500, OSError("down"), 200)
    assert webhook._deliver(b"{}", "e1") == 200
    assert len(post.calls) == 3 and sleeps == [1.0, 4.0]


def test_no_retry_on_4xx():
    webhook, post, sleeps = make_webhook(400)
    assert webhook._deliver(b"{}", "e1") == 400
    assert len(post.calls) == 1 and sleeps == []


def test_gives_up_after_three_attempts():
    webhook, post, _ = make_webhook(503)
    assert webhook._deliver(b"{}", "e1") == 503 and len(post.calls) == 3


def test_circuit_breaker_pauses_then_resumes(caplog):
    webhook, post, _ = make_webhook(503)
    with caplog.at_level("WARNING"):
        for _ in range(webhook_service.BREAKER_FAILURES):
            webhook._deliver(b"{}", "e")
    assert "tạm ngừng" in caplog.text
    calls = len(post.calls)
    assert webhook._deliver(b"{}", "e") is None and len(post.calls) == calls  # đang ngắt: không gọi mạng
    webhook._paused_until = 0.0
    webhook._deliver(b"{}", "e")
    assert len(post.calls) > calls


def test_disabled_without_config():
    post = FakePost(200)
    for url, secret in ((None, None), ("https://x", None), (None, "s"), ("", "")):
        webhook = WebhookService(url, secret, post=post, run_inline=True)
        webhook.notify({"id": "1", "so_luong": 1})
        assert not webhook.enabled
    assert post.calls == []


def test_webhook_failure_never_breaks_user_action(env):
    env.webhook._post = FakePost(OSError("boom"))
    env.webhook._sleep = lambda s: None
    login()
    assert env.http.post("/api/usage/heartbeat").json() == {"counted": True}
    assert len(env.client.tables["usage_events"]) == 1
    # webhook ném lỗi bất ngờ cũng không lan ra
    env.webhook.notify = lambda *a, **k: (_ for _ in ()).throw(RuntimeError("x"))
    env.rag.usage.log("hoi_dap", USER)
    assert len(env.client.tables["usage_events"]) == 2


def test_background_delivery_does_not_block():
    started, release = threading.Event(), threading.Event()

    def slow_post(*args, **kwargs):
        started.set()
        release.wait(5)
        return SimpleNamespace(status_code=200)

    webhook = WebhookService("https://x", "s", post=slow_post)
    webhook.notify({"id": "1", "so_luong": 1})  # trả về ngay dù POST đang treo
    assert started.wait(2)
    release.set()


def test_admin_webhook_test_endpoint(env):
    assert env.http.post("/api/admin/webhook/test").status_code == 401
    body = env.http.post("/api/admin/webhook/test", headers=ADMIN).json()
    assert body["ok"] is True and body["ma_phan_hoi"] == 200 and "s3cret" not in json.dumps(body)
    assert json.loads(env.post.calls[-1].body)["su_kien"]["tinh_nang"] == "thu_ket_noi"
    env.rag.webhook = WebhookService(None, None)
    assert env.http.post("/api/admin/webhook/test", headers=ADMIN).status_code == 409


def test_sign_helper():
    assert sign("k", b"abc") == "sha256=" + hmac.new(b"k", b"abc", hashlib.sha256).hexdigest()
