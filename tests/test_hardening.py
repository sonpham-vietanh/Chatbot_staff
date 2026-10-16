"""Các sửa lỗi sau đợt kiểm thử tổng thể: cache đăng nhập có giới hạn, chặn dò admin token, IP người gọi, lỗi tiếng Việt, hỏi đáp hồ sơ/điểm."""
from types import SimpleNamespace

import pytest
from fastapi import HTTPException
from fastapi.testclient import TestClient

from app.api import routes
from app.main import _validation_message, app


def test_user_cache_never_grows_past_its_limit_and_drops_oldest():
    for index in range(routes.USER_CACHE_MAX_ENTRIES + 50):
        routes._cache_user(f"k{index}", {"id": index})
    assert len(routes._USER_CACHE) <= routes.USER_CACHE_MAX_ENTRIES
    assert "k0" not in routes._USER_CACHE and f"k{routes.USER_CACHE_MAX_ENTRIES + 49}" in routes._USER_CACHE


def test_admin_token_guessing_is_blocked_after_too_many_wrong_tries():
    caller = "203.0.113.9"
    for _ in range(routes.ADMIN_FAIL_LIMIT - 1):
        assert routes._admin_fail_wait(caller, record=True) == 0
    assert routes._admin_fail_wait(caller, record=True) > 0          # lần sai thứ 10 -> khoá
    assert routes._admin_fail_wait(caller, record=False) > 0
    assert routes._admin_fail_wait("198.51.100.1", record=False) == 0  # IP khác không bị ảnh hưởng


def _request(headers):
    return SimpleNamespace(headers={k.lower(): v for k, v in headers.items()}, client=SimpleNamespace(host="10.0.0.5"))


def test_client_ip_ignores_a_spoofed_first_forwarded_for_entry():
    assert routes._client_ip(_request({"X-Forwarded-For": "6.6.6.6, 203.0.113.7"})) == "203.0.113.7"
    assert routes._client_ip(_request({"CF-Connecting-IP": "198.51.100.2", "X-Forwarded-For": "6.6.6.6"})) == "198.51.100.2"
    assert routes._client_ip(_request({})) == "10.0.0.5"


def test_validation_errors_are_vietnamese_and_do_not_echo_the_input():
    errors = [
        {"type": "string_too_long", "loc": ("body", "question"), "msg": "x", "input": "A" * 5000, "ctx": {"max_length": 2000}},
        {"type": "missing", "loc": ("body", "reason"), "msg": "x", "input": None},
    ]
    message = _validation_message(errors)
    assert "Câu hỏi quá dài (tối đa 2000 ký tự)" in message and "Lý do đang thiếu" in message and "AAAA" not in message


def test_chat_rejects_bad_thread_id_with_a_short_vietnamese_message():
    app.dependency_overrides[routes.require_user] = lambda: {"id": "u1", "email": "lan@truongvietanh.com"}
    try:
        response = TestClient(app).post("/api/chat-staff", json={"question": "Xin chào bạn", "thread_id": "khong-phai-uuid"})
    finally:
        app.dependency_overrides.pop(routes.require_user, None)
    assert response.status_code == 422 and response.json()["detail"].startswith("Dữ liệu chưa hợp lệ")


def test_security_headers_are_added():
    response = TestClient(app).get("/api/auth/config", headers={"X-Forwarded-Proto": "https"})
    assert response.headers["x-content-type-options"] == "nosniff"
    assert "strict-origin" in response.headers["referrer-policy"] and "max-age" in response.headers["strict-transport-security"]


class _Points:
    def summary(self, email, recent=10):
        return {"total": 7, "recent": [{"label": "Gửi báo sai", "points": 1}]}


class _Profiles:
    def __init__(self, in_directory=True):
        self.in_directory = in_directory

    def get(self, email):
        return {"in_directory": self.in_directory, "display_name": "Lan", "department": "HR", "job_title": "Chuyên viên",
                "manager": {"display_name": "Tiến"}, "tenure": "2 năm", "phone": "0909123456", "birth_date": "1990-01-01"}


def test_own_points_and_profile_questions_are_answered_from_the_askers_own_data_only():
    rag = SimpleNamespace(points=_Points(), profiles=_Profiles())
    user = {"email": "lan@truongvietanh.com"}
    points = routes._own_info_answer(rag, user, "Điểm của tôi là bao nhiêu?")
    assert "7 điểm" in points["answer"]
    profile = routes._own_info_answer(rag, user, "Hồ sơ của tôi")
    assert "Chuyên viên" in profile["answer"] and "Tiến" in profile["answer"]
    assert "0909" not in profile["answer"] and "1990" not in profile["answer"]          # không đưa thông tin riêng tư vào chat
    assert routes._own_info_answer(rag, user, "Phòng ban của chị Lan là gì") is None  # người khác: đi đường hỏi đáp thường
    assert "chưa thấy bạn trong danh bạ" in routes._own_info_answer(SimpleNamespace(profiles=_Profiles(False)), user, "chức danh của tôi")["answer"]


def test_infrastructure_errors_mention_migrations_only_when_a_table_is_really_missing():
    assert routes._is_schema_error(RuntimeError("PGRST205 Could not find the table 'public.wigs' in the schema cache"))
    assert not routes._is_schema_error(RuntimeError("connection reset by peer"))
    assert "migration" not in routes._infra_detail(RuntimeError("timeout"), "chạy migration")


# ---------- đợt sửa sau 10 người test ----------
from datetime import date

from app.rag.pipeline import AdvancedRAGPipeline
from app.rag.suggestions import suggest_followups
from app.services.profile_service import leave_entitlement


def test_leave_entitlement_follows_the_seniority_table():
    today = date(2026, 10, 15)
    assert "13 ngày" in leave_entitlement("2017-04-15", today)      # 9 năm -> 13 ngày
    assert "12 ngày" in leave_entitlement("2024-01-01", today)      # 2 năm
    assert "14 ngày" in leave_entitlement("2014-01-01", today)      # 12 năm
    assert "6 tháng" in leave_entitlement("2026-04-15", today)      # dưới 1 năm: theo số tháng
    assert leave_entitlement(None, today) is None and leave_entitlement("2999-01-01", today) is None


def test_search_synonyms_bridge_how_staff_ask_and_how_documents_say_it():
    assert "thưởng Tết" in AdvancedRAGPipeline._with_synonyms("lương tháng 13 có không")
    assert "bảo hiểm xã hội" in AdvancedRAGPipeline._with_synonyms("thử việc có đóng bhxh ko")
    assert "báo trước nghỉ việc" in AdvancedRAGPipeline._with_synonyms("what is the notice period if I resign")
    assert AdvancedRAGPipeline._with_synonyms("nghỉ phép năm mấy ngày") == "nghỉ phép năm mấy ngày"   # câu bình thường giữ nguyên


def test_followup_suggestions_are_on_topic_and_never_repeat_the_question():
    picked = suggest_followups("Xin nghỉ phép thế nào?")
    assert 1 <= len(picked) <= 3 and "Xin nghỉ phép như thế nào?" not in picked and any("phép" in item for item in picked)
    assert len(suggest_followups("xin chào")) == 3


class _Wigs:
    def __init__(self, reports=()):
        self._reports = list(reports)

    def reports_of(self, email):
        return self._reports

    def chat_team(self, email):
        return "TEAM"

    def chat_view(self, email, kind):
        return f"VIEW-{kind}"

    def chat_summary(self, email, name=None):
        return "ACTIVE"

    def list_for(self, email):
        return []


def test_wig_questions_are_split_into_active_done_dropped_team_and_reminders():
    rag = SimpleNamespace(wigs=_Wigs())
    user = {"email": "lan@truongvietanh.com"}
    ask = lambda q: routes._own_wig_answer(rag, user, q)
    assert ask("WIG của tôi tới đâu rồi")["answer"] == "ACTIVE"
    assert ask("tôi đã hoàn thành WIG nào chưa")["answer"] == "VIEW-done"
    assert ask("WIG bị hủy của tôi")["answer"] == "VIEW-dropped"
    assert ask("WIG của team tôi tới đâu rồi")["answer"] == "TEAM"
    assert "chưa có tính năng nhắc nhở" in ask("nhắc tôi cập nhật wig tuần sau nhé")["answer"]
    assert ask("WIG cua toi con bao nhieu nua la xong")["answer"] == "ACTIVE"           # "xong" ở cuối không phải hỏi WIG đã xong
    assert ask("WIG là gì") is None and ask("WIG của chị Lan thế nào") is None


def test_single_field_profile_questions_get_a_short_answer_from_the_askers_own_record():
    class Profiles:
        def get(self, email):
            return {"in_directory": True, "display_name": "Lan", "department": "HR", "job_title": "Chuyên viên", "campus": "Phan Huy Ích",
                    "manager": {"display_name": "Tiến"}, "employment_start_date": "2025-04-15", "tenure": "1 năm 6 tháng"}

    rag = SimpleNamespace(profiles=Profiles(), wigs=_Wigs([{"email": "a@x.com", "display_name": "Nam", "job_title": "GV"}]))
    user = {"email": "lan@truongvietanh.com"}
    ask = lambda q: (routes._own_info_answer(rag, user, q) or {}).get("answer")
    assert ask("sếp tôi là ai") == "Quản lý trực tiếp của bạn: **Tiến**."
    assert "2025-04-15" in ask("tui vào làm ngày nào") and "1 năm 6 tháng" in ask("tui vào làm ngày nào")
    assert ask("chức danh của tôi là gì") == "Chức danh của bạn: **Chuyên viên**."
    assert "Nam" in ask("tôi quản lý những ai")
    assert "riêng tư" in ask("số điện thoại của tôi là gì") and "lan@truongvietanh.com" in ask("email của tôi là gì")
    assert ask("tôi vào làm 6 tháng thì được mấy ngày phép") is None        # câu chính sách: để mô hình trả lời, có khối NGƯỜI HỎI
    assert ask("chức danh của chị Lan là gì") is None and ask("phòng ban của anh Nam") is None


def test_asker_block_gives_the_model_seniority_leave_and_reports_but_no_private_data():
    class Directory:
        def get(self, email):
            if email == "lan@truongvietanh.com":
                return {"active": True, "display_name": "Lan", "department": "HR", "job_title": "Chuyên viên", "employment_start_date": "2017-04-15",
                        "employment_type": "co_huu", "manager_email": "tien@truongvietanh.com", "campus": None}
            return {"active": True, "display_name": "Tiến", "email": email}

    rag = SimpleNamespace(directory=Directory(), wigs=_Wigs([{"email": "a@x.com", "display_name": "Nam", "job_title": "GV"}]))
    routes._ASKER_CACHE.clear()
    text = routes._build_asker(rag, {"email": "lan@truongvietanh.com"})
    assert "13 ngày phép/năm" in text and "Quản lý trực tiếp: Tiến" in text and "Nam (GV)" in text
    assert "sinh" not in text.lower() and "địa chỉ" not in text.lower()
    assert "Chưa có trong danh bạ" in routes._build_asker(SimpleNamespace(directory=SimpleNamespace(get=lambda e: None), wigs=None), {"email": "moi@x.com"})


def test_citation_with_a_misspelled_source_name_is_mapped_back_to_the_real_source():
    results = [{"id": "n:0", "text": "x", "score": 0.9, "metadata": {"source_file": "Quy trình nghỉ việc", "heading": "8 bước", "version": "0.1"}}]
    got = AdvancedRAGPipeline._parse_citations("Trả lời.\n[Nguồn: Quy quy trình nghỉ việc > 8 bước > 0.1]", results)
    assert got == [{"source": "Quy trình nghỉ việc", "heading": "8 bước", "version": "0.1"}]


def test_change_request_without_a_reason_is_rejected():
    from app.services.directory_service import DirectoryError

    class Directory:
        def get(self, email):
            return {"email": email, "active": True, "campus": "A"}

    from app.services.change_request_service import ChangeRequestService

    service = ChangeRequestService(SimpleNamespace(select=lambda *a, **k: []), Directory())
    with pytest.raises(DirectoryError) as error:
        service.create("lan@truongvietanh.com", "campus", "B")
    assert error.value.status_code == 422 and "lý do" in error.value.detail


def test_changing_the_directory_also_drops_the_cached_asker_block():
    routes._ASKER_CACHE["lan@truongvietanh.com"] = ("cũ", 0.0)
    routes.clear_user_cache()
    assert routes._ASKER_CACHE == {}


def test_greeting_does_not_wait_for_the_askers_profile_lookup(monkeypatch):
    called = []
    monkeypatch.setattr(routes, "_asker_context", lambda rag, user: called.append(1))
    app.dependency_overrides[routes.require_user] = lambda: {"id": "u1", "email": "lan@truongvietanh.com", "user_metadata": {}}
    rag = SimpleNamespace(chat=lambda *a, **k: {"answer": "x", "grounded": False, "citations": []},
                          _quick_reply=AdvancedRAGPipeline._quick_reply, supabase=None, usage=None, wigs=None, profiles=None, points=None)
    app.dependency_overrides[routes.get_rag_service] = lambda: rag
    app.dependency_overrides[routes.get_chat_history_service] = lambda: SimpleNamespace(
        thread_belongs_to=lambda *a: True, create_thread_with_id=lambda *a: None, add_turn=lambda *a: None, list_messages=lambda *a: [])
    try:
        TestClient(app).post("/api/chat-staff", json={"question": "xin chào"})
        assert called == []                                  # câu chào: không lấy hồ sơ
        TestClient(app).post("/api/chat-staff", json={"question": "tôi được mấy ngày phép năm nay"})
        assert called == [1]                                 # câu thật: có lấy
    finally:
        app.dependency_overrides.clear()
