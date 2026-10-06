"""API báo cáo cho Major OS: key chỉ-đọc, lọc thời gian, phân trang con trỏ không trùng/sót, cảnh báo, ghi sự kiện."""
import uuid
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient

from app.api.routes import get_rag_service, require_user
from app.config import get_settings
from app.main import app
from app.services.report_service import RateLimiter, ReportKeyService, ReportService, hash_key
from app.services.usage_service import FEATURES, UsageService, decode_cursor, encode_cursor, parse_time

ADMIN = {"X-Admin-Token": "admin-secret"}
BASE = datetime(2026, 10, 6, 1, 0, 0, tzinfo=timezone.utc)


def _parse(value: str):
    return datetime.fromisoformat(value.replace("Z", "+00:00"))


class FakeClient:
    """Đủ PostgREST cho các truy vấn của báo cáo: eq/gt/gte/lt, and(...)/or(...) lồng nhau, order, limit."""

    def __init__(self):
        self.tables = {"usage_events": [], "answer_feedback": [], "report_keys": [], "knowledge_notes": [], "chat_logs": []}
        self.fail = False

    @staticmethod
    def _split_top(text: str) -> list[str]:
        parts, depth, current = [], 0, ""
        for ch in text:
            if ch == "(":
                depth += 1
            elif ch == ")":
                depth -= 1
            if ch == "," and depth == 0:
                parts.append(current)
                current = ""
            else:
                current += ch
        return parts + [current]

    def _eval(self, expr: str, row: dict) -> bool:
        if expr.startswith("and(") or expr.startswith("or("):
            op, inner = expr.split("(", 1)
            results = [self._eval(part, row) for part in self._split_top(inner[:-1])]
            return all(results) if op == "and" else any(results)
        column, operator, value = expr.split(".", 2)
        actual = row.get(column)
        if actual is None:
            return False
        if column.endswith("_at") or column == "occurred_at":
            actual, value = _parse(actual), _parse(value)
        return {"eq": actual == value, "gt": actual > value, "gte": actual >= value, "lt": actual < value}[operator]

    def select(self, table, params=None):
        if self.fail:
            raise RuntimeError("db down")
        params = dict(params or {})
        rows = [dict(r) for r in self.tables[table]]
        for key, value in params.items():
            if key in ("select", "order", "limit"):
                continue
            if key == "and":
                rows = [r for r in rows if self._eval("and" + value, r)]
            elif value.startswith("eq."):
                rows = [r for r in rows if str(r.get(key)) == value[3:]]
            elif value.startswith("gte."):
                rows = [r for r in rows if r.get(key) and _parse(r[key]) >= _parse(value[4:])]
        for spec in reversed(params.get("order", "").split(",")):
            if spec:
                column, direction = spec.split(".")
                rows.sort(key=lambda r: (_parse(r[column]) if column.endswith("_at") else r[column]), reverse=direction == "desc")
        if "limit" in params:
            rows = rows[: int(params["limit"])]
        columns = params.get("select", "*")
        if columns != "*":  # như PostgREST thật: chỉ trả đúng các cột được chọn
            wanted = columns.split(",")
            rows = [{c: r[c] for c in wanted if c in r} for r in rows]
        return rows

    def insert(self, table, rows, returning=True):
        if self.fail:
            raise RuntimeError("db down")
        stored = []
        for row in rows:
            now = datetime.now(timezone.utc).isoformat()  # như DEFAULT now() của bảng thật
            row = {"id": str(uuid.uuid4()), "created_at": now, "occurred_at": now, "status": "active", **row}
            self.tables[table].append(row)
            stored.append(dict(row))
        return stored if returning else None

    def update(self, table, params, patch):
        out = []
        for row in self.tables[table]:
            if all(str(row.get(key)) == value[3:] for key, value in params.items()):  # mọi điều kiện eq. đều phải khớp
                row.update(patch)
                out.append(dict(row))
        return out

    def delete(self, table, params):
        key, value = next(iter(params.items()))
        self.tables[table][:] = [r for r in self.tables[table] if str(r.get(key)) != value[3:]]


def make_event(i: int, minutes: float, email="lan@truongvietanh.com", feature="hoi_dap", **extra):
    return {"id": f"evt_{i:04d}", "occurred_at": (BASE + timedelta(minutes=minutes)).isoformat(), "user_email": email,
            "user_name": "Nguyễn Thị Lan" if email else None, "feature": feature, "quantity": 1, "meta": {"tra_loi_duoc": True}, **extra}


@pytest.fixture
def env():
    client = FakeClient()
    keys = ReportKeyService(client)
    rag = SimpleNamespace(usage=UsageService(client), report=ReportService(client), report_keys=keys, supabase=client)
    app.dependency_overrides[get_rag_service] = lambda: rag
    app.dependency_overrides[get_settings] = lambda: SimpleNamespace(admin_token="admin-secret", public_base_url="https://staffbot.vietanh.org")
    created = keys.create("Major OS")
    http = TestClient(app)
    http.headers.update({"Authorization": f"Bearer {created['key']}"})
    try:
        yield SimpleNamespace(http=http, client=client, keys=keys, key=created, rag=rag)
    finally:
        app.dependency_overrides.clear()


# ---------- key ----------
def test_key_is_stored_hashed_and_shown_only_once(env):
    stored = env.client.tables["report_keys"][0]
    assert "key" not in stored and stored["key_hash"] == hash_key(env.key["key"]) and env.key["key"].startswith("vr_")
    assert stored["key_hint"] == env.key["key"][-4:]
    listing = env.keys.list()
    assert all("key" not in row and "key_hash" not in row for row in listing)


def test_requests_without_a_valid_key_are_rejected(env):
    anonymous = TestClient(app)
    for path in ("kiem-tra", "tinh-nang", "su-kien", "gop-y", "canh-bao", "tong-quan"):
        assert anonymous.get(f"/api/report/{path}").status_code == 401
        assert anonymous.get(f"/api/report/{path}", headers={"Authorization": "Bearer vr_sai"}).status_code == 401
        assert anonymous.get(f"/api/report/{path}", headers={"Authorization": "Basic abc"}).status_code == 401
    assert anonymous.get("/api/report/kiem-tra", headers={"Authorization": f"Bearer {env.key['key']}"}).json()["ok"] is True
    # Key chỉ có trong URL không được chấp nhận
    assert anonymous.get(f"/api/report/kiem-tra?key={env.key['key']}").status_code == 401


def test_revoked_key_stops_working_immediately(env):
    assert env.http.get("/api/report/kiem-tra").status_code == 200
    assert env.http.post(f"/api/admin/report-keys/{'00000000-0000-0000-0000-000000000000'}/revoke", headers=ADMIN).status_code == 404
    env.keys.revoke(env.client.tables["report_keys"][0]["id"])
    assert env.http.get("/api/report/kiem-tra").status_code == 401


def test_rate_limit_returns_429_with_retry_after(env):
    env.keys.limiter = RateLimiter(limit=3)
    assert [env.http.get("/api/report/kiem-tra").status_code for _ in range(3)] == [200, 200, 200]
    blocked = env.http.get("/api/report/kiem-tra")
    assert blocked.status_code == 429 and int(blocked.headers["Retry-After"]) >= 1


def test_database_outage_is_a_503_not_a_500(env):
    env.client.fail = True
    assert env.http.get("/api/report/kiem-tra").status_code == 503
    env.client.fail = False
    env.client.tables["usage_events"].append(make_event(1, 0))
    env.client.fail = True
    # key đã xác thực xong rồi DB mới hỏng ở truy vấn dữ liệu
    env.keys.authenticate = lambda raw: {"id": "k", "label": "x", "last_used_at": None}
    assert env.http.get("/api/report/su-kien").status_code == 503


# ---------- sự kiện: lọc + phân trang ----------
def test_cursor_pagination_never_duplicates_or_skips_even_with_identical_timestamps(env):
    rows = [make_event(i, minutes=i // 3) for i in range(25)]  # cứ 3 dòng cùng 1 thời điểm
    env.client.tables["usage_events"].extend(rows)
    seen, cursor, pages = [], None, 0
    while True:
        url = "/api/report/su-kien?gioi_han=7" + (f"&con_tro={cursor}" if cursor else "")
        body = env.http.get(url).json()
        seen += [r["id"] for r in body["du_lieu"]]
        pages += 1
        cursor = body["trang_sau"]
        if not cursor:
            break
    assert seen == [r["id"] for r in rows] and len(set(seen)) == 25 and pages == 4
    # Lấy lại lần nữa ra y hệt (không sinh bản trùng)
    again = env.http.get("/api/report/su-kien?gioi_han=500").json()["du_lieu"]
    assert [r["id"] for r in again] == seen


def test_time_window_is_half_open_and_accepts_unencoded_plus_offset(env):
    env.client.tables["usage_events"].extend(make_event(i, minutes=i * 10) for i in range(6))  # 08:00, 08:10, ... giờ VN
    # tu = 08:10 +07:00 (viết thẳng dấu + trên URL, bị giải mã thành khoảng trắng), den = 08:30 +07:00 -> 08:10, 08:20
    body = env.http.get("/api/report/su-kien?tu=2026-10-06T08:10:00+07:00&den=2026-10-06T08:30:00+07:00").json()
    assert [r["id"] for r in body["du_lieu"]] == ["evt_0001", "evt_0002"]
    utc = env.http.get("/api/report/su-kien", params={"tu": "2026-10-06T01:10:00Z", "den": "2026-10-06T01:30:00Z"}).json()
    assert [r["id"] for r in utc["du_lieu"]] == ["evt_0001", "evt_0002"]


def test_bad_parameters_are_422(env):
    for query in ("tu=2026-10-06T08:00:00", "tu=hom-qua", "tu=2026-10-07T00:00:00Z&den=2026-10-06T00:00:00Z", "con_tro=khong-hop-le",
                  "gioi_han=0", "gioi_han=501", "gioi_han=5000"):
        assert env.http.get(f"/api/report/su-kien?{query}").status_code == 422, query


def test_event_shape_matches_the_documented_format(env):
    env.client.tables["usage_events"].extend([
        make_event(1, 15),
        make_event(2, 20, email=None, feature="hoi_dap_nhung", meta={"tra_loi_duoc": False, "nguon": "Major OS"}),
    ])
    body = env.http.get("/api/report/su-kien").json()
    first, second = body["du_lieu"]
    assert first == {"id": "evt_0001", "luc": "2026-10-06T08:15:00+07:00", "nguoi": {"email": "lan@truongvietanh.com", "ten": "Nguyễn Thị Lan"},
                     "tinh_nang": "hoi_dap", "so_lan": 1, "chi_tiet": {"tra_loi_duoc": True}}
    assert second["nguoi"] is None and second["tinh_nang"] == "hoi_dap_nhung"
    assert body["trang_sau"] is None and body["gio_may_chu"].endswith("+07:00")
    assert "cau_hoi" not in str(body) and "answer" not in str(body)  # không lộ nội dung hội thoại


def test_feature_filter(env):
    env.client.tables["usage_events"].extend([make_event(1, 1), make_event(2, 2, feature="bao_sai"), make_event(3, 3)])
    ids = [r["id"] for r in env.http.get("/api/report/su-kien?tinh_nang=bao_sai").json()["du_lieu"]]
    assert ids == ["evt_0002"]


def test_cursor_helpers_roundtrip_and_reject_garbage():
    assert decode_cursor(encode_cursor("2026-10-05T01:15:00.123456+00:00", "evt_1")) == ("2026-10-05T01:15:00.123456+00:00", "evt_1")
    from app.services.usage_service import ReportQueryError
    for bad in ("", "%%%", encode_cursor("khong-phai-ngay", "x")):
        with pytest.raises(ReportQueryError):
            decode_cursor(bad)
    assert parse_time("2026-10-06T08:00:00 07:00", "tu") == parse_time("2026-10-06T08:00:00+07:00", "tu")


# ---------- góp ý / cảnh báo / tổng quan ----------
def test_feedback_report_maps_codes_and_truncates(env):
    env.client.tables["answer_feedback"].append({
        "id": "b8f0c2de-0000-0000-0000-000000000001", "created_at": (BASE + timedelta(minutes=30)).isoformat(), "user_email": "Lan@TruongVietAnh.com",
        "reason": "wrong_info", "note": "Sai số ngày", "status": "resolved", "handled_at": (BASE + timedelta(hours=1)).isoformat(),
        "question": "x" * 500, "answer": "KHÔNG ĐƯỢC LỘ", "citations": []})
    body = env.http.get("/api/report/gop-y").json()
    row = body["du_lieu"][0]
    assert row["ly_do"] == "thong_tin_sai" and row["trang_thai"] == "da_sua" and len(row["cau_hoi"]) == 300
    assert row["nguoi"]["email"] == "lan@truongvietanh.com" and row["xu_ly_luc"].endswith("+07:00")
    assert "KHÔNG ĐƯỢC LỘ" not in str(body)


def test_alerts_reflect_current_state_and_disappear_when_resolved(env):
    now = datetime.now(timezone.utc)
    assert env.http.get("/api/report/canh-bao").json()["du_lieu"] == []
    env.client.tables["answer_feedback"] += [
        {"id": "f1", "created_at": (now - timedelta(days=5)).isoformat(), "status": "open"},
        {"id": "f2", "created_at": (now - timedelta(hours=1)).isoformat(), "status": "open"},
        {"id": "f3", "created_at": (now - timedelta(days=9)).isoformat(), "status": "resolved"}]
    env.client.tables["knowledge_notes"] += [
        {"id": "n1", "title": "[CẦN BỔ SUNG] x", "created_by": "AI_Bot", "status": "draft", "created_at": now.isoformat()},
        {"id": "n2", "title": "Quy định mới", "created_by": "hr@x.com", "status": "draft", "created_at": now.isoformat()},
        {"id": "n3", "title": "Đã duyệt", "created_by": "hr@x.com", "status": "approved", "created_at": now.isoformat()}]
    env.client.tables["chat_logs"] += [{"id": f"l{i}", "grounded": i < 10, "created_at": now.isoformat()} for i in range(40)]  # 25% có nguồn
    alerts = {a["id"]: a for a in env.http.get("/api/report/canh-bao").json()["du_lieu"]}
    assert set(alerts) == {"bao_sai_chua_xu_ly", "cau_hoi_chua_tra_loi", "tai_lieu_cho_duyet", "ty_le_tra_loi_duoc_thap"}
    assert alerts["bao_sai_chua_xu_ly"]["so_luong"] == 2 and alerts["bao_sai_chua_xu_ly"]["muc"] == "canh_bao"
    assert alerts["cau_hoi_chua_tra_loi"]["so_luong"] == 1 and alerts["tai_lieu_cho_duyet"]["so_luong"] == 1
    env.client.tables["answer_feedback"][:] = [r for r in env.client.tables["answer_feedback"] if r["status"] != "open"]
    assert "bao_sai_chua_xu_ly" not in {a["id"] for a in env.http.get("/api/report/canh-bao").json()["du_lieu"]}


def test_summary_counts_unused_features_as_zero_and_ranks_users(env):
    env.client.tables["usage_events"] += [
        make_event(1, 1), make_event(2, 2), make_event(3, 3, email="minh@truongvietanh.com", meta={"tra_loi_duoc": False}),
        make_event(4, 4, feature="bao_sai", meta={})]
    body = env.http.get("/api/report/tong-quan").json()
    by = {f["tinh_nang"]: f for f in body["theo_tinh_nang"]}
    assert by["hoi_dap"]["so_lan"] == 3 and by["hoi_dap"]["so_nguoi"] == 2 and by["xoa_tri_thuc"]["so_lan"] == 0
    assert set(by) == set(FEATURES) and body["tong_so_lan"] == 4 and body["so_nguoi_dung"] == 2
    assert body["ty_le_tra_loi_duoc"] == round(2 / 3, 3) and body["nguoi_dung_nhieu_nhat"][0]["email"] == "lan@truongvietanh.com"


def test_features_endpoint_matches_registry(env):
    keys = [f["khoa"] for f in env.http.get("/api/report/tinh-nang").json()["du_lieu"]]
    assert keys == list(FEATURES)


# ---------- admin ----------
def test_admin_key_endpoints_need_admin_token_and_key_is_returned_once(env):
    assert TestClient(app).post("/api/admin/report-keys", json={"label": "x"}).status_code == 401
    assert TestClient(app).get("/api/admin/report-spec").status_code == 401
    admin = TestClient(app, headers=ADMIN)
    created = admin.post("/api/admin/report-keys", json={"label": "  Major OS prod  "}).json()
    assert created["key"].startswith("vr_") and created["label"] == "Major OS prod"
    assert all("key" not in k and "key_hash" not in k for k in admin.get("/api/admin/report-keys").json())
    assert admin.post("/api/admin/report-keys", json={"label": "  "}).status_code == 422
    assert admin.delete(f"/api/admin/report-keys/{created['id']}").status_code == 200


def test_spec_download_has_real_base_url_and_all_features(env):
    response = TestClient(app, headers=ADMIN).get("/api/admin/report-spec")
    text = response.text
    assert response.status_code == 200 and "attachment" in response.headers["content-disposition"]
    assert "https://staffbot.vietanh.org" in text and "{{" not in text
    for key in FEATURES:
        assert f"`{key}`" in text
    for path in ("kiem-tra", "tinh-nang", "su-kien", "gop-y", "canh-bao", "tong-quan"):
        assert f"/api/report/{path}" in text


# ---------- ghi sự kiện ----------
def test_usage_log_is_best_effort_and_normalizes_the_user():
    client = FakeClient()
    usage = UsageService(client)
    usage.log("hoi_dap", {"id": "u1", "email": " Lan@TruongVietAnh.com ", "user_metadata": {"full_name": "Lan"}}, {"tra_loi_duoc": True})
    row = client.tables["usage_events"][0]
    assert row["user_email"] == "lan@truongvietanh.com" and row["user_name"] == "Lan" and row["feature"] == "hoi_dap" and row["quantity"] == 1
    client.fail = True
    usage.log("hoi_dap", None)  # DB hỏng: không ném lỗi


class RecordingUsage:
    def __init__(self):
        self.events = []

    def log(self, feature, user=None, meta=None, count=1):
        self.events.append((feature, (user or {}).get("email"), meta))


def test_chat_feedback_and_manage_actions_are_tracked(env):
    from tests.test_manage import FakeAdmin, FakeClient as ManageClient, make_service  # noqa: F401
    service, _ = make_service()
    recorder = RecordingUsage()

    class Chat:
        def chat(self, *args, **kwargs):
            return {"answer": "ok", "grounded": True, "citations": [{"source": "a", "heading": "h", "version": "v1"}, {"source": "b", "heading": "h", "version": "v1"}]}

    class FeedbackSvc:
        def create(self, user, payload):
            return {"id": "fb1"}

    class History:
        pass

    rag = SimpleNamespace(usage=recorder, manage=service, supabase=ManageClient(), chat=Chat().chat, feedback=FeedbackSvc(),
                          chat_history=History())
    from app.api.routes import get_chat_history_service, get_feedback_service
    app.dependency_overrides[get_rag_service] = lambda: rag
    app.dependency_overrides[get_feedback_service] = lambda: rag.feedback
    app.dependency_overrides[get_chat_history_service] = lambda: SimpleNamespace(
        create_thread_with_id=lambda *a, **k: None, thread_belongs_to=lambda *a: True, add_turn=lambda *a: None)
    app.dependency_overrides[require_user] = lambda: {"id": "u1", "email": "hr@truongvietanh.com", "user_metadata": {}}
    http = TestClient(app)

    assert http.post("/api/chat-staff", json={"question": "nghỉ phép?"}).status_code == 200
    assert http.post("/api/feedback", json={"question": "q", "answer": "a", "reason": "wrong_info"}).status_code == 200
    created = http.post("/api/manage/notes", json={"title": "Quy định X", "department": "HR", "content": "nội dung"}).json()["id"]
    assert http.put(f"/api/manage/notes/{created}", json={"content": "đã sửa"}).status_code == 200
    assert http.delete(f"/api/manage/notes/{created}").status_code == 200
    features = [(f, who) for f, who, _ in recorder.events]
    assert features == [("hoi_dap", "hr@truongvietanh.com"), ("bao_sai", "hr@truongvietanh.com"), ("tao_tri_thuc", "hr@truongvietanh.com"),
                        ("sua_tri_thuc", "hr@truongvietanh.com"), ("xoa_tri_thuc", "hr@truongvietanh.com")]
    assert recorder.events[0][2] == {"tra_loi_duoc": True, "so_nguon": 2}
    # Nội dung câu hỏi KHÔNG đi vào nhật ký sử dụng
    assert "nghỉ phép" not in str(recorder.events)
    # Người không phải leader bị từ chối thì không được ghi là đã sửa
    before = len(recorder.events)
    app.dependency_overrides[require_user] = lambda: {"id": "u2", "email": "nobody@truongvietanh.com", "user_metadata": {}}
    assert http.post("/api/manage/notes", json={"title": "x", "department": "HR", "content": "y"}).status_code == 403
    assert len(recorder.events) == before


def test_widget_questions_are_tracked_without_a_person(env):
    from app.api.routes import require_widget_key

    recorder = RecordingUsage()
    rag = SimpleNamespace(usage=recorder, supabase=env.client,
                          chat=lambda *a, **k: {"answer": "ok", "grounded": False, "citations": []})
    app.dependency_overrides[get_rag_service] = lambda: rag
    app.dependency_overrides[require_widget_key] = lambda: {"id": "k1", "label": "Major OS"}
    response = TestClient(app).post("/api/widget/chat", json={"question": "xin chào"})
    assert response.status_code == 200
    assert recorder.events == [("hoi_dap_nhung", None, {"tra_loi_duoc": False, "so_nguon": 0, "nguon": "Major OS"})]


def test_summary_scans_every_page_not_just_the_first(env, monkeypatch):
    import app.services.usage_service as usage_module

    monkeypatch.setattr(usage_module, "MAX_PAGE_SIZE", 7)
    env.client.tables["usage_events"] += [make_event(i, i) for i in range(30)]
    body = env.http.get("/api/report/tong-quan").json()
    assert body["tong_so_lan"] == 30 and body["bi_cat_bot"] is False
    monkeypatch.setattr(usage_module, "SUMMARY_SCAN_LIMIT", 14)
    assert env.http.get("/api/report/tong-quan").json()["bi_cat_bot"] is True


def test_garbage_cursor_cannot_inject_into_the_filter_and_equal_bounds_are_empty_not_error(env):
    env.client.tables["usage_events"].append(make_event(1, 0))
    evil = encode_cursor("2026-10-06T01:00:00+00:00", "x),occurred_at.lt.1970-01-01T00:00:00+00:00")
    assert env.http.get(f"/api/report/su-kien?con_tro={evil}").status_code == 422
    assert env.http.get(f"/api/report/su-kien?con_tro={encode_cursor('2026-10-06T01:00:00', 'evt_1')}").status_code == 422  # thiếu múi giờ
    same = "2026-10-06T01:00:00Z"
    body = env.http.get("/api/report/su-kien", params={"tu": same, "den": same})
    assert body.status_code == 200 and body.json()["du_lieu"] == []


def test_only_whitelisted_detail_keys_leave_the_app(env):
    env.client.tables["usage_events"].append(make_event(
        1, 1, feature="sua_tri_thuc", meta={"phong_ban": "HR", "tieu_de": "Bảng lương Q3 của CEO", "noi_dung": "bí mật", "tra_loi_duoc": True}))
    row = env.http.get("/api/report/su-kien").json()["du_lieu"][0]
    assert row["chi_tiet"] == {"phong_ban": "HR", "tra_loi_duoc": True}
    assert "lương" not in str(row) and "bí mật" not in str(row)


def test_feedback_person_is_null_without_email_and_name_comes_from_usage_log(env):
    base = (BASE + timedelta(minutes=5)).isoformat()
    env.client.tables["answer_feedback"] += [
        {"id": "00000000-0000-0000-0000-00000000000a", "created_at": base, "user_email": "lan@truongvietanh.com", "reason": "other", "status": "open", "question": "q"},
        {"id": "00000000-0000-0000-0000-00000000000b", "created_at": base, "user_email": None, "reason": "other", "status": "open", "question": "q"}]
    env.client.tables["usage_events"].append(make_event(1, 1))  # có tên "Nguyễn Thị Lan"
    rows = {r["id"][-1]: r for r in env.http.get("/api/report/gop-y").json()["du_lieu"]}
    assert rows["a"]["nguoi"] == {"email": "lan@truongvietanh.com", "ten": "Nguyễn Thị Lan"} and rows["b"]["nguoi"] is None


def test_revoking_twice_keeps_the_first_revocation_time(env):
    key_id = env.client.tables["report_keys"][0]["id"]
    assert env.keys.revoke(key_id) is True
    first = env.client.tables["report_keys"][0]["revoked_at"]
    assert env.keys.revoke(key_id) is False and env.client.tables["report_keys"][0]["revoked_at"] == first


def test_repeated_bad_keys_from_one_ip_get_blocked_before_touching_the_database(env):
    anonymous = TestClient(app)
    codes = [anonymous.get("/api/report/kiem-tra", headers={"Authorization": "Bearer vr_sai"}).status_code for _ in range(35)]
    assert codes[:30] == [401] * 30 and codes[30:] == [429] * 5
    # Cùng IP mà key đúng thì cũng bị chặn tạm — chấp nhận được: Major OS dùng IP cố định và không bao giờ gọi sai 30 lần/phút
    assert anonymous.get("/api/report/kiem-tra", headers={"Authorization": f"Bearer {env.key['key']}"}).status_code == 429
    # Một IP khác không bị ảnh hưởng
    assert anonymous.get("/api/report/kiem-tra", headers={"Authorization": f"Bearer {env.key['key']}", "X-Forwarded-For": "10.1.1.1"}).status_code == 200


def test_summary_defaults_to_the_last_30_days(env):
    now = datetime.now(timezone.utc)
    old = make_event(1, 0)
    old["occurred_at"] = (now - timedelta(days=45)).isoformat()
    recent = make_event(2, 0)
    recent["occurred_at"] = (now - timedelta(days=2)).isoformat()
    env.client.tables["usage_events"] += [old, recent]
    assert env.http.get("/api/report/tong-quan").json()["tong_so_lan"] == 1
    assert env.http.get("/api/report/tong-quan", params={"tu": (now - timedelta(days=60)).isoformat()}).json()["tong_so_lan"] == 2
