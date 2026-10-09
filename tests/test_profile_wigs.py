"""Hồ sơ nhân viên, WIG tự nhập (xem được bởi chính chủ / quản lý trực tiếp / admin) và sổ điểm đóng góp."""
import uuid
from datetime import date, datetime, timedelta, timezone
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient

from app.api.routes import get_rag_service, require_user
from app.config import get_settings
from app.main import app
from app.services.feedback_service import FeedbackService
from app.services.points_service import SUBMIT_DAILY_CAP, PointsService
from app.services.profile_service import ProfileService, tenure_text
from app.services.wig_service import WigError, WigService, format_number, progress_percent

ADMIN = {"X-Admin-Token": "admin-secret"}


class FakeClient:
    """Đủ PostgREST cho các truy vấn của dịch vụ: eq./gte., sắp xếp, giới hạn, và ràng buộc unique của user_points."""

    def __init__(self):
        self.tables = {t: [] for t in ("employee_directory", "staff_profiles", "wigs", "wig_updates", "user_points", "answer_feedback")}

    @staticmethod
    def _match(row, params):
        for key, value in params.items():
            if key in ("select", "order", "limit"):
                continue
            if value.startswith("eq."):
                if str(row.get(key)).lower() != value[3:].lower():
                    return False
            elif value.startswith("gte."):
                if str(row.get(key)) < value[4:]:
                    return False
        return True

    def select(self, table, params=None):
        params = params or {}
        rows = [dict(r) for r in self.tables[table] if self._match(r, params)]
        if "order" in params:
            column, _, direction = params["order"].partition(".")
            rows.sort(key=lambda r: str(r.get(column)), reverse=direction == "desc")
        return rows[: int(params.get("limit", 1000))]

    def insert(self, table, rows, returning=True):
        out = []
        for row in rows:
            row = {"id": str(uuid.uuid4()), "created_at": datetime.now(timezone.utc).isoformat(), "status": "open" if table == "answer_feedback" else row.get("status"), **row}
            if table == "user_points" and any(all(r[k] == row[k] for k in ("email", "reason", "ref_type", "ref_id")) for r in self.tables[table]):
                raise RuntimeError("duplicate key value violates unique constraint")
            if table == "wigs":
                row.setdefault("status", "active")
            self.tables[table].append(row)
            out.append(dict(row))
        return out if returning else None

    def update(self, table, params, patch):
        hit = [r for r in self.tables[table] if self._match(r, params)]
        for r in hit:
            r.update(patch)
        return [dict(r) for r in hit]


WIG = {"title": "Tăng tỷ lệ đúng hạn", "period_label": "Q4/2026", "metric_name": "Tỷ lệ báo cáo đúng hạn", "unit": "%",
       "start_value": 60, "target_value": 90}


def make(admin="boss@truongvietanh.com"):
    client = FakeClient()
    client.tables["employee_directory"] = [
        {"email": "lan@truongvietanh.com", "display_name": "Nguyễn Thị Lan", "department": "HR", "job_title": "Chuyên viên",
         "employment_start_date": "2024-01-15", "manager_email": "mgr@truongvietanh.com", "campus": "Việt Anh", "work_phone": None, "active": True},
        {"email": "mgr@truongvietanh.com", "display_name": "Trần Quản Lý", "department": "HR", "job_title": "Trưởng phòng",
         "employment_start_date": "2020-03-01", "manager_email": None, "campus": None, "work_phone": None, "active": True},
        {"email": "off@truongvietanh.com", "display_name": "Đã nghỉ", "department": "HR", "job_title": "x",
         "employment_start_date": None, "manager_email": "mgr@truongvietanh.com", "campus": None, "work_phone": None, "active": False},
    ]
    return WigService(client, admin), client


# ---------- tính toán thuần ----------
def test_progress_works_for_higher_and_lower_is_better_and_is_clamped():
    assert progress_percent(60, 90, 75) == 50.0
    assert progress_percent(100, 40, 70) == 50.0           # chỉ số càng thấp càng tốt (vd. số lỗi)
    assert progress_percent(0, 10, 15) == 100.0 and progress_percent(0, 10, -5) == 0.0
    assert progress_percent(5, 5, 5) == 100.0 and progress_percent(5, 5, 4) == 0.0


def test_vietnamese_number_format_and_tenure():
    assert format_number(1234567) == "1.234.567" and format_number(1234.5) == "1.234,5" and format_number(60) == "60"
    today = date(2026, 10, 8)
    assert tenure_text("2024-01-15", today) == "2 năm 8 tháng" and tenure_text("2026-10-01", today) == "dưới 1 tháng"
    assert tenure_text("2025-10-08", today) == "1 năm" and tenure_text("2026-05-30", today) == "4 tháng"
    assert tenure_text(None, today) is None and tenure_text("2027-01-01", today) is None and tenure_text("không phải ngày", today) is None


# ---------- WIG ----------
def test_create_wig_records_first_update_and_reports_progress():
    service, client = make()
    wig = service.create("LAN@truongvietanh.com", WIG)
    assert wig["owner_email"] == "lan@truongvietanh.com" and wig["current_value"] == 60 and wig["progress"] == 0
    assert client.tables["wig_updates"][0]["note"] == "Tạo WIG"
    done = service.add_progress("lan@truongvietanh.com", wig["id"], 75, "Đã gửi 2/3 phòng")
    assert done["current_value"] == 75 and done["progress"] == 50.0
    history = service.updates("lan@truongvietanh.com", wig["id"])
    assert [h["value"] for h in history] == [75, 60] or [h["value"] for h in history] == [60, 75]


def test_wig_input_is_validated():
    service, _ = make()
    for bad in ({**WIG, "title": "  "}, {**WIG, "metric_name": ""}, {**WIG, "target_value": "abc"}, {**WIG, "target_value": float("inf")},
                {**WIG, "period_label": ""}):
        with pytest.raises(WigError) as error:
            service.create("lan@truongvietanh.com", bad)
        assert error.value.status_code == 422


def test_active_wig_limit():
    service, _ = make()
    for i in range(10):
        service.create("lan@truongvietanh.com", {**WIG, "title": f"WIG {i}"})
    with pytest.raises(WigError) as error:
        service.create("lan@truongvietanh.com", WIG)
    assert error.value.status_code == 409
    service.create("mgr@truongvietanh.com", WIG)  # giới hạn tính theo từng người


def test_only_the_owner_can_edit_or_update_progress_and_closed_wigs_reject_progress():
    service, _ = make()
    wig = service.create("lan@truongvietanh.com", WIG)
    for actor in ("mgr@truongvietanh.com", "boss@truongvietanh.com", "stranger@truongvietanh.com"):
        with pytest.raises(WigError) as error:
            service.add_progress(actor, wig["id"], 80)
        assert error.value.status_code == 403
        with pytest.raises(WigError):
            service.update(actor, wig["id"], {"title": "x"})
    service.update("lan@truongvietanh.com", wig["id"], {"status": "done"})
    with pytest.raises(WigError) as closed:
        service.add_progress("lan@truongvietanh.com", wig["id"], 80)
    assert closed.value.status_code == 409
    service.update("lan@truongvietanh.com", wig["id"], {"status": "active"})
    assert service.add_progress("lan@truongvietanh.com", wig["id"], 80)["current_value"] == 80


def test_dropped_wigs_are_hidden_unless_asked():
    service, _ = make()
    wig = service.create("lan@truongvietanh.com", WIG)
    service.update("lan@truongvietanh.com", wig["id"], {"status": "dropped"})
    assert service.list_for("lan@truongvietanh.com") == []
    assert len(service.list_for("lan@truongvietanh.com", include_dropped=True)) == 1


def test_who_may_view_whose_wig():
    service, _ = make()
    wig = service.create("lan@truongvietanh.com", WIG)
    assert service.can_view("lan@truongvietanh.com", "lan@truongvietanh.com")
    assert service.can_view("MGR@truongvietanh.com", "lan@truongvietanh.com")        # quản lý trực tiếp
    assert service.can_view("boss@truongvietanh.com", "lan@truongvietanh.com")        # admin
    assert not service.can_view("stranger@truongvietanh.com", "lan@truongvietanh.com")
    assert not service.can_view("lan@truongvietanh.com", "mgr@truongvietanh.com")     # cấp dưới không xem được cấp trên
    assert service.updates("mgr@truongvietanh.com", wig["id"])
    with pytest.raises(WigError) as denied:
        service.updates("stranger@truongvietanh.com", wig["id"])
    assert denied.value.status_code == 403


def test_manager_sees_only_active_wigs_of_active_direct_reports():
    service, _ = make()
    service.create("lan@truongvietanh.com", WIG)
    finished = service.create("lan@truongvietanh.com", {**WIG, "title": "Xong rồi"})
    service.update("lan@truongvietanh.com", finished["id"], {"status": "done"})
    team = service.team("mgr@truongvietanh.com")
    assert [p["email"] for p in team] == ["lan@truongvietanh.com"]          # người đã nghỉ không có trong danh sách
    assert [w["title"] for w in team[0]["wigs"]] == [WIG["title"]]
    assert service.team("lan@truongvietanh.com") == []


def test_chat_summary_lists_progress_and_flags_overdue_without_leaking_others():
    service, _ = make()
    assert "chưa có WIG" in service.chat_summary("lan@truongvietanh.com")
    service.create("lan@truongvietanh.com", {**WIG, "current_value": 75, "due_date": "2020-01-01"})
    service.create("mgr@truongvietanh.com", {**WIG, "title": "WIG của sếp"})
    text = service.chat_summary("lan@truongvietanh.com")
    assert "**50%**" in text and "75/90 %" in text and "đã quá hạn" in text and "WIG của sếp" not in text


# ---------- hồ sơ ----------
def test_profile_merges_hr_data_with_self_edited_fields():
    service, client = make()
    profiles = ProfileService(client)
    profile = profiles.get("Lan@truongvietanh.com")
    assert profile["display_name"] == "Nguyễn Thị Lan" and profile["manager"]["display_name"] == "Trần Quản Lý"
    assert profile["in_directory"] and profile["has_team"] is False and profiles.get("mgr@truongvietanh.com")["has_team"] is True
    profiles.update_self("lan@truongvietanh.com", " 0901 234 567 ", "Phụ trách tuyển dụng")
    profiles.update_self("lan@truongvietanh.com", "0909", None)                      # lần 2: cập nhật, không tạo dòng mới
    assert len(client.tables["staff_profiles"]) == 1 and profiles.get("lan@truongvietanh.com")["phone"] == "0909"
    outsider = profiles.get("khac@truongvietanh.com")
    assert outsider["in_directory"] is False and outsider["display_name"] is None and outsider["tenure"] is None


# ---------- điểm ----------
def test_points_are_awarded_once_per_event_and_totalled():
    client = FakeClient()
    points = PointsService(client)
    assert points.award("Lan@truongvietanh.com", "bao_sai_gui", "feedback", "f1") is True
    assert points.award("lan@truongvietanh.com", "bao_sai_gui", "feedback", "f1") is False     # cùng một báo cáo: không cộng lại
    assert points.award("lan@truongvietanh.com", "bao_sai_xac_nhan", "feedback", "f1") is True
    assert points.award("lan@truongvietanh.com", "ly_do_la", "feedback", "f1") is False and points.award(None, "bao_sai_gui") is False
    summary = points.summary("lan@truongvietanh.com")
    assert summary["total"] == 4 and {r["label"] for r in summary["recent"]} == {"Gửi báo sai", "Báo sai được xác nhận và đã sửa"}


def test_daily_cap_stops_point_farming_but_confirmed_reports_still_count():
    points = PointsService(FakeClient())
    results = [points.award("lan@truongvietanh.com", "bao_sai_gui", "feedback", f"f{i}") for i in range(SUBMIT_DAILY_CAP + 3)]
    assert results.count(True) == SUBMIT_DAILY_CAP
    assert points.award("lan@truongvietanh.com", "bao_sai_xac_nhan", "feedback", "f0") is True


def test_award_never_raises_when_the_database_fails():
    class Broken:
        def select(self, *a, **k):
            raise RuntimeError("db down")

    assert PointsService(Broken()).award("lan@truongvietanh.com", "bao_sai_gui", "feedback", "f1") is False


# ---------- HTTP ----------
@pytest.fixture
def http():
    service, client = make()
    chat_calls = []

    def chat(question, history=None, asker_department=None):
        chat_calls.append(question)
        return {"answer": "Trả lời thường.", "grounded": True, "citations": []}

    rag = SimpleNamespace(wigs=service, profiles=ProfileService(client), points=PointsService(client), chat=chat,
                          feedback=FeedbackService(client), supabase=None, usage=None,
                          chat_history=SimpleNamespace(create_thread_with_id=lambda *a, **k: None, thread_belongs_to=lambda *a: True, add_turn=lambda *a: None))
    state = {"email": "lan@truongvietanh.com"}
    from app.api.routes import get_chat_history_service, get_feedback_service
    app.dependency_overrides[get_rag_service] = lambda: rag
    app.dependency_overrides[get_feedback_service] = lambda: rag.feedback
    app.dependency_overrides[get_chat_history_service] = lambda: rag.chat_history
    app.dependency_overrides[get_settings] = lambda: SimpleNamespace(admin_token="admin-secret")
    app.dependency_overrides[require_user] = lambda: {"id": "u1", "email": state["email"], "user_metadata": {}}
    try:
        yield TestClient(app), state, chat_calls, client
    finally:
        app.dependency_overrides.clear()


def test_api_full_wig_flow_and_permissions(http):
    api, state, _, _ = http
    created = api.post("/api/me/wigs", json={**WIG, "due_date": "2026-12-31"})
    assert created.status_code == 200 and created.json()["progress"] == 0
    wig_id = created.json()["id"]
    assert api.post(f"/api/me/wigs/{wig_id}/progress", json={"value": 75, "note": "Tuần 2"}).json()["progress"] == 50.0
    assert api.get("/api/me/wigs").json()["items"][0]["current_value"] == 75
    assert api.put(f"/api/me/wigs/{wig_id}", json={"target_value": 100}).json()["target_value"] == 100
    assert len(api.get(f"/api/wigs/{wig_id}/updates").json()["items"]) == 2

    state["email"] = "mgr@truongvietanh.com"                                              # quản lý trực tiếp: xem được, không sửa được
    assert len(api.get(f"/api/wigs/{wig_id}/updates").json()["items"]) == 2
    assert api.post(f"/api/me/wigs/{wig_id}/progress", json={"value": 99}).status_code == 403
    assert api.get("/api/team/wigs").json()["items"][0]["wigs"][0]["title"] == WIG["title"]
    assert api.get("/api/me/wigs").json()["items"] == []

    state["email"] = "stranger@truongvietanh.com"
    assert api.get(f"/api/wigs/{wig_id}/updates").status_code == 403
    assert api.put(f"/api/me/wigs/{wig_id}", json={"title": "x"}).status_code == 403
    assert api.post("/api/me/wigs", json={**WIG, "title": ""}).status_code == 422
    assert api.post("/api/me/wigs/not-a-uuid/progress", json={"value": 1}).status_code == 422


def test_api_profile_and_points(http):
    api, _, _, _ = http
    assert api.put("/api/me/profile", json={"phone": "0901", "bio": "Xin chào"}).status_code == 200
    profile = api.get("/api/me/profile").json()
    assert profile["display_name"] == "Nguyễn Thị Lan" and profile["phone"] == "0901" and profile["tenure"] and profile["points"]["total"] == 0
    assert api.put("/api/me/profile", json={"bio": "x" * 501}).status_code == 422


def test_reporting_a_wrong_answer_earns_points_and_admin_confirmation_earns_more_once(http):
    api, _, _, client = http
    report = {"question": "Nghỉ phép?", "answer": "12 ngày", "citations": [], "reason": "wrong_info"}
    assert api.post("/api/feedback", json=report).status_code == 200
    assert api.get("/api/me/profile").json()["points"]["total"] == 1
    feedback_id = client.tables["answer_feedback"][0]["id"]
    for _ in range(2):                                                                       # bấm "đã sửa" hai lần vẫn chỉ cộng một lần
        assert api.post(f"/api/admin/feedback/{feedback_id}/status", headers=ADMIN, json={"status": "resolved"}).status_code == 200
    assert api.get("/api/me/profile").json()["points"]["total"] == 4
    other = {"question": "Q2", "answer": "A2", "citations": [], "reason": "other"}
    api.post("/api/feedback", json=other)
    second_id = client.tables["answer_feedback"][1]["id"]
    api.post(f"/api/admin/feedback/{second_id}/status", headers=ADMIN, json={"status": "dismissed"})   # bỏ qua: không thưởng thêm
    assert api.get("/api/me/profile").json()["points"]["total"] == 5


def test_chat_answers_own_wig_questions_from_the_database_and_other_questions_normally(http):
    api, state, chat_calls, _ = http
    assert "chưa có WIG" in api.post("/api/chat-staff", json={"question": "WIG của tôi tới đâu rồi?"}).json()["answer"]
    api.post("/api/me/wigs", json={**WIG, "current_value": 75})
    answer = api.post("/api/chat-staff", json={"question": "wig của mình thế nào"}).json()["answer"]
    assert "Tăng tỷ lệ đúng hạn" in answer and "50%" in answer and chat_calls == []         # không gọi mô hình

    state["email"] = "stranger@truongvietanh.com"                                           # người khác hỏi: chỉ thấy của chính mình
    assert "Tăng tỷ lệ đúng hạn" not in api.post("/api/chat-staff", json={"question": "WIG của tôi?"}).json()["answer"]
    for question in ("WIG của chị Lan tới đâu rồi?", "WIG là gì?", "Quy định nghỉ phép?"):
        assert api.post("/api/chat-staff", json={"question": question}).json()["answer"] == "Trả lời thường."
    assert len(chat_calls) == 3


def test_missing_migration_gives_a_clear_503_not_a_crash(http):
    api, _, _, client = http

    class Broken:
        def select(self, *a, **k):
            raise RuntimeError('relation "wigs" does not exist')

    app.dependency_overrides[get_rag_service] = lambda: SimpleNamespace(
        wigs=WigService(Broken()), profiles=ProfileService(Broken()), points=PointsService(Broken()), chat=None)
    response = api.get("/api/me/wigs")
    assert response.status_code == 503 and "20261007000000_profiles_wigs_points.sql" in response.json()["detail"]
    assert api.get("/api/me/profile").status_code == 503
