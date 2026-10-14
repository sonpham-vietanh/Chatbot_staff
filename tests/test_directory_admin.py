"""Danh bạ nhân sự do HR quản lý, nhập hàng loạt, yêu cầu chỉnh sửa hồ sơ và nhật ký thay đổi."""
import uuid
from datetime import datetime, timezone
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient

from app.api.routes import get_rag_service, require_user
from app.config import get_settings
from app.main import app
from app.services.change_request_service import ChangeRequestService
from app.services.directory_service import REQUESTABLE_FIELDS, DirectoryError, DirectoryService
from app.services.points_service import PointsService
from app.services.profile_service import ProfileService

ADMIN = {"X-Admin-Token": "admin-secret"}
TABLES = ("employee_directory", "staff_profiles", "profile_change_requests", "profile_audit_log", "user_points")


class FakeClient:
    """PostgREST giả đủ dùng: eq./in., sắp xếp, giới hạn."""

    def __init__(self):
        self.tables = {t: [] for t in TABLES}

    @staticmethod
    def _match(row, params):
        for key, value in params.items():
            if key in ("select", "order", "limit"):
                continue
            if value.startswith("eq."):
                if str(row.get(key)).lower() != value[3:].lower():
                    return False
            elif value.startswith("in.("):
                if str(row.get(key)) not in value[4:-1].split(","):
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
            row = {"id": str(uuid.uuid4()), "created_at": datetime.now(timezone.utc).isoformat(), **row}
            if table == "profile_change_requests":
                row.setdefault("status", "pending")
            if table == "employee_directory":
                row.setdefault("active", True)
            self.tables[table].append(row)
            out.append(dict(row))
        return out if returning else None

    def update(self, table, params, patch):
        hit = [r for r in self.tables[table] if self._match(r, params)]
        for r in hit:
            r.update(patch)
        return [dict(r) for r in hit]


def person(email, name, **extra):
    return {"email": email, "display_name": name, "department": "HR", "job_title": "Chuyên viên", "employment_start_date": "2024-01-15",
            "employment_end_date": None, "active": True, "manager_email": None, "campus": "Việt Anh", "work_phone": None,
            "employee_code": None, "level_name": None, "contract_type": None, "employment_type": None, "team_name": None,
            "extra_roles": None, "subjects": None, "grade_levels": None, **extra}


def make():
    client = FakeClient()
    client.tables["employee_directory"] = [
        person("lan@truongvietanh.com", "Nguyễn Thị Lan", manager_email="mgr@truongvietanh.com"),
        person("mgr@truongvietanh.com", "Trần Quản Lý", job_title="Trưởng phòng", campus=None),
        person("off@truongvietanh.com", "Đã Nghỉ", active=False, employment_end_date="2025-06-30"),
    ]
    directory = DirectoryService(client, "truongvietanh.com")
    return directory, ChangeRequestService(client, directory), client


def audit_actions(client, email=None):
    return [(r["email"], r["actor"], r["action"]) for r in client.tables["profile_audit_log"] if email in (None, r["email"])]


# ---------- tạo / sửa / ngừng / kích hoạt lại ----------
def test_create_normalizes_email_rejects_duplicates_and_bad_domain_and_audits():
    directory, _, client = make()
    row = directory.create({"email": " NEW@TruongVietAnh.com ", "display_name": "Người Mới", "department": "Kế toán", "job_title": "NV",
                            "employment_type": "Thỉnh giảng"})
    assert row["email"] == "new@truongvietanh.com" and row["employment_type"] == "thinh_giang" and row["active"] is True
    assert audit_actions(client, "new@truongvietanh.com") == [("new@truongvietanh.com", "admin", "create")]
    with pytest.raises(DirectoryError) as dup:
        directory.create({"email": "lan@truongvietanh.com", "display_name": "x", "department": "x", "job_title": "x"})
    assert dup.value.status_code == 409
    for bad in ({"email": "a@gmail.com"}, {"email": "khong-phai-email"}, {"email": "ok@truongvietanh.com", "display_name": ""}):
        with pytest.raises(DirectoryError) as error:
            directory.create({"display_name": "x", "department": "x", "job_title": "x", **bad})
        assert error.value.status_code == 422


def test_update_records_old_and_new_values_and_skips_noop():
    directory, _, client = make()
    row = directory.update("LAN@truongvietanh.com", {"job_title": "Trưởng nhóm", "team_name": "Tổ Toán", "campus": "Việt Anh"})
    assert row["job_title"] == "Trưởng nhóm" and row["team_name"] == "Tổ Toán"
    (_, actor, action), = audit_actions(client)
    assert (actor, action) == ("admin", "hr_update")
    assert client.tables["profile_audit_log"][0]["changes"] == {
        "job_title": {"old": "Chuyên viên", "new": "Trưởng nhóm"}, "team_name": {"old": None, "new": "Tổ Toán"}}   # campus không đổi: không có trong changes
    directory.update("lan@truongvietanh.com", {"job_title": "Trưởng nhóm"})
    assert len(client.tables["profile_audit_log"]) == 1
    cleared = directory.update("lan@truongvietanh.com", {"team_name": ""})
    assert cleared["team_name"] is None
    with pytest.raises(DirectoryError) as missing:
        directory.update("ghost@truongvietanh.com", {"job_title": "x"})
    assert missing.value.status_code == 404
    for bad in ({"employment_type": "robot"}, {"employment_start_date": "15/01/2024"}, {"display_name": ""}, {"subjects": "x" * 201}):
        with pytest.raises(DirectoryError) as error:
            directory.update("lan@truongvietanh.com", bad)
        assert error.value.status_code == 422, bad


def test_manager_loops_are_blocked():
    directory, _, client = make()
    with pytest.raises(DirectoryError) as self_manager:
        directory.update("lan@truongvietanh.com", {"manager_email": "lan@truongvietanh.com"})
    assert self_manager.value.status_code == 422
    with pytest.raises(DirectoryError) as loop:                       # lan -> mgr, nên mgr -> lan là vòng lặp
        directory.update("mgr@truongvietanh.com", {"manager_email": "lan@truongvietanh.com"})
    assert loop.value.status_code == 422 and "vòng lặp" in loop.value.detail
    assert directory.update("mgr@truongvietanh.com", {"manager_email": "boss@truongvietanh.com"})["manager_email"] == "boss@truongvietanh.com"
    assert client.tables["employee_directory"][1]["manager_email"] == "boss@truongvietanh.com"


def test_deactivate_and_reactivate_cycle():
    directory, _, client = make()
    row = directory.deactivate("lan@truongvietanh.com", "2026-09-30")
    assert row["active"] is False and row["employment_end_date"] == "2026-09-30"
    with pytest.raises(DirectoryError) as again:
        directory.deactivate("lan@truongvietanh.com")
    assert again.value.status_code == 409
    back = directory.reactivate("lan@truongvietanh.com")
    assert back["active"] is True and back["employment_end_date"] is None
    with pytest.raises(DirectoryError):
        directory.reactivate("lan@truongvietanh.com")
    assert [a for _, _, a in audit_actions(client)] == ["deactivate", "reactivate"]
    assert directory.deactivate("mgr@truongvietanh.com")["employment_end_date"]          # không gửi ngày: mặc định hôm nay
    with pytest.raises(DirectoryError) as bad_date:
        directory.deactivate("lan@truongvietanh.com", "30-09-2026")
    assert bad_date.value.status_code == 422


def test_list_filters_by_activity_and_searches_without_case_or_accents():
    directory, _, _ = make()
    assert directory.list()["total"] == 2 and directory.list(active="false")["total"] == 1 and directory.list(active="all")["total"] == 3
    assert [r["email"] for r in directory.list(q="NGUYEN thi lan")["items"]] == ["lan@truongvietanh.com"]
    assert [r["email"] for r in directory.list(q="trưởng phòng")["items"]] == ["mgr@truongvietanh.com"]
    assert directory.list(q="khong co", active="all")["total"] == 0
    page = directory.list(active="all", limit=2, offset=2)
    assert page["total"] == 3 and len(page["items"]) == 1


# ---------- nhập hàng loạt ----------
def test_import_dry_run_writes_nothing_but_reports_everything():
    directory, _, client = make()
    before = [dict(r) for r in client.tables["employee_directory"]]
    rows = [{"email": "moi@truongvietanh.com", "display_name": "Người Mới", "department": "IT", "job_title": "Dev", "employment_type": "Cơ hữu"},
            {"email": "lan@truongvietanh.com", "job_title": "Trưởng nhóm"},
            {"email": "mgr@truongvietanh.com", "display_name": "Trần Quản Lý"}]
    result = directory.import_rows(rows, dry_run=True)
    assert (result["dry_run"], result["created"], result["updated"], result["unchanged"], result["errors"]) == (True, 1, 1, 1, [])
    assert [p["action"] for p in result["preview"]] == ["create", "update", "unchanged"]
    assert result["preview"][1]["changes"] == {"job_title": {"old": "Chuyên viên", "new": "Trưởng nhóm"}}
    assert client.tables["employee_directory"] == before and client.tables["profile_audit_log"] == []


def test_import_blank_cells_never_erase_existing_data_and_labels_are_accepted():
    directory, _, client = make()
    result = directory.import_rows([
        {"email": "LAN@truongvietanh.com", "display_name": "", "campus": "  ", "job_title": "Trưởng nhóm", "employment_type": "Cơ hữu"},
        {"email": "moi@truongvietanh.com", "display_name": "Người Mới", "department": "IT", "job_title": "Dev", "employment_type": "thỉnh giảng"}],
        dry_run=False)
    assert (result["created"], result["updated"]) == (1, 1)
    lan = next(r for r in client.tables["employee_directory"] if r["email"] == "lan@truongvietanh.com")
    assert lan["display_name"] == "Nguyễn Thị Lan" and lan["campus"] == "Việt Anh"          # ô trống không xoá
    assert lan["job_title"] == "Trưởng nhóm" and lan["employment_type"] == "co_huu"           # nhãn "Cơ hữu" -> mã
    new = next(r for r in client.tables["employee_directory"] if r["email"] == "moi@truongvietanh.com")
    assert new["employment_type"] == "thinh_giang" and new["active"] is True
    assert sorted(audit_actions(client)) == [("lan@truongvietanh.com", "admin", "import"), ("moi@truongvietanh.com", "admin", "import")]


def test_import_reports_duplicate_emails_wrong_domain_and_incomplete_rows_and_writes_only_good_rows():
    directory, _, client = make()
    rows = [{"email": "a@truongvietanh.com", "display_name": "A", "department": "IT", "job_title": "Dev"},
            {"email": "A@truongvietanh.com", "display_name": "A hai", "department": "IT", "job_title": "Dev"},      # trùng dòng 1
            {"email": "b@gmail.com", "display_name": "B", "department": "IT", "job_title": "Dev"},                 # sai miền
            {"email": "c@truongvietanh.com", "display_name": "C"},                                                # người mới thiếu cột bắt buộc
            {"email": "", "display_name": "Không email"},
            {"email": "d@truongvietanh.com", "display_name": "D", "department": "IT", "job_title": "Dev", "employment_type": "lạ"},
            {"email": "mgr@truongvietanh.com", "manager_email": "lan@truongvietanh.com"}]                          # tạo vòng lặp quản lý
    result = directory.import_rows(rows, dry_run=False)
    assert result["created"] == 1 and result["updated"] == 0
    assert [e["row"] for e in result["errors"]] == [2, 3, 4, 5, 6, 7]
    assert "trùng" in result["errors"][0]["message"] and result["errors"][0]["email"] == "a@truongvietanh.com"
    emails = [r["email"] for r in client.tables["employee_directory"]]
    assert "a@truongvietanh.com" in emails and not any(e.startswith(("b@", "c@", "d@")) for e in emails)
    assert len(result["preview"]) == 7 and result["preview"][1]["action"] == "error"


def test_import_limits_rows_and_caps_preview_at_fifty():
    directory, _, _ = make()
    many = [{"email": f"u{i}@truongvietanh.com", "display_name": f"U{i}", "department": "IT", "job_title": "Dev"} for i in range(60)]
    result = directory.import_rows(many, dry_run=True)
    assert result["created"] == 60 and len(result["preview"]) == 50
    with pytest.raises(DirectoryError) as too_many:
        directory.import_rows([{"email": "x@truongvietanh.com"}] * 1001)
    assert too_many.value.status_code == 422


# ---------- yêu cầu chỉnh sửa ----------
def test_request_lifecycle_create_and_validation():
    _, requests, client = make()
    created = requests.create("LAN@truongvietanh.com", "job_title", "  Trưởng   nhóm ", "Được bổ nhiệm")
    assert created["label"] == "Chức danh" and created["old_value"] == "Chuyên viên" and created["new_value"] == "Trưởng nhóm" and created["status"] == "pending"
    with pytest.raises(DirectoryError) as dup:
        requests.create("lan@truongvietanh.com", "job_title", "Khác nữa")
    assert dup.value.status_code == 409
    expected = [("employment_type", "co_huu"), ("email", "x@x.com"), ("manager_email", "a@b.com"), ("active", "false"), ("employee_code", "1"),
                ("contract_type", "x"), ("level_name", "x"), ("employment_end_date", "2026-01-01"), ("job_title", ""), ("job_title", "x" * 101),
                ("employment_start_date", "15/01/2024"), ("job_title", "Chuyên viên"), ("subjects", "x" * 201), ("campus", "ok")]
    for field, value in expected[:-1]:
        with pytest.raises(DirectoryError) as error:
            requests.create("lan@truongvietanh.com", field, value)
        assert error.value.status_code == 422, (field, value)
    with pytest.raises(DirectoryError) as nobody:
        requests.create("ghost@truongvietanh.com", "campus", "ok")
    assert nobody.value.status_code == 404
    with pytest.raises(DirectoryError) as inactive:
        requests.create("off@truongvietanh.com", "campus", "ok")
    assert inactive.value.status_code == 404
    assert len(client.tables["profile_change_requests"]) == 1
    assert set(REQUESTABLE_FIELDS) == {"display_name", "job_title", "department", "campus", "work_phone", "employment_start_date",
                                       "team_name", "extra_roles", "subjects", "grade_levels"}


def test_cancel_only_by_owner_and_only_while_pending():
    _, requests, client = make()
    created = requests.create("lan@truongvietanh.com", "campus", "Thái Sơn")
    with pytest.raises(DirectoryError) as stranger:
        requests.cancel("mgr@truongvietanh.com", created["id"])
    assert stranger.value.status_code == 403 and client.tables["profile_change_requests"][0]["status"] == "pending"
    with pytest.raises(DirectoryError) as ghost:
        requests.cancel("lan@truongvietanh.com", str(uuid.uuid4()))
    assert ghost.value.status_code == 404
    assert requests.cancel("LAN@truongvietanh.com", created["id"]) == {"status": "cancelled"}
    with pytest.raises(DirectoryError) as again:
        requests.cancel("lan@truongvietanh.com", created["id"])
    assert again.value.status_code == 409
    assert requests.create("lan@truongvietanh.com", "campus", "Thái Sơn")["status"] == "pending"      # huỷ xong gửi lại được


def test_approve_applies_value_to_directory_and_audits():
    directory, requests, client = make()
    created = requests.create("lan@truongvietanh.com", "employment_start_date", "2023-12-01")
    done = requests.approve(created["id"], "Đã đối chiếu hợp đồng")
    assert done["status"] == "approved" and done["review_note"] == "Đã đối chiếu hợp đồng"
    assert directory.get("lan@truongvietanh.com")["employment_start_date"] == "2023-12-01"
    stored = client.tables["profile_change_requests"][0]
    assert stored["reviewed_by"] == "admin" and stored["reviewed_at"]
    log = [r for r in client.tables["profile_audit_log"] if r["action"] == "request_approved"][0]
    assert log["email"] == "lan@truongvietanh.com" and log["changes"] == {"employment_start_date": {"old": "2024-01-15", "new": "2023-12-01"}}
    with pytest.raises(DirectoryError) as twice:
        requests.approve(created["id"])
    assert twice.value.status_code == 409


def test_approve_is_refused_when_data_changed_since_the_request():
    directory, requests, client = make()
    created = requests.create("lan@truongvietanh.com", "job_title", "Trưởng nhóm")
    directory.update("lan@truongvietanh.com", {"job_title": "Phó phòng"})
    with pytest.raises(DirectoryError) as stale:
        requests.approve(created["id"])
    assert stale.value.status_code == 409 and "dữ liệu đã đổi" in stale.value.detail.lower()
    assert directory.get("lan@truongvietanh.com")["job_title"] == "Phó phòng"
    assert client.tables["profile_change_requests"][0]["status"] == "pending"


def test_reject_requires_a_reason_and_leaves_directory_untouched():
    directory, requests, client = make()
    created = requests.create("lan@truongvietanh.com", "campus", "Thái Sơn")
    for blank in (None, "", "   "):
        with pytest.raises(DirectoryError) as error:
            requests.reject(created["id"], blank)
        assert error.value.status_code == 422
    done = requests.reject(created["id"], "Chưa có quyết định điều chuyển")
    assert done["status"] == "rejected" and done["review_note"] == "Chưa có quyết định điều chuyển"
    assert directory.get("lan@truongvietanh.com")["campus"] == "Việt Anh"
    assert audit_actions(client)[-1] == ("lan@truongvietanh.com", "admin", "request_rejected")
    with pytest.raises(DirectoryError) as again:
        requests.reject(created["id"], "lại")
    assert again.value.status_code == 409


def test_admin_list_counts_pending_and_joins_names():
    _, requests, _ = make()
    first = requests.create("lan@truongvietanh.com", "campus", "Thái Sơn")
    requests.create("mgr@truongvietanh.com", "campus", "Nhân Lễ")
    requests.reject(first["id"], "Không đúng")
    pending = requests.admin_list("pending")
    assert pending["pending_count"] == 1 and [i["display_name"] for i in pending["items"]] == ["Trần Quản Lý"]
    everything = requests.admin_list("all")
    assert len(everything["items"]) == 2 and {i["label"] for i in everything["items"]} == {"Cơ sở"}
    assert requests.admin_list("rejected")["items"][0]["review_note"] == "Không đúng"


# ---------- hồ sơ: consent, audit self_update, requests ----------
def test_consent_keeps_the_first_timestamp_and_creates_the_row_if_missing():
    _, _, client = make()
    profiles = ProfileService(client)
    first = profiles.give_consent("Lan@truongvietanh.com")
    assert profiles.give_consent("lan@truongvietanh.com") == first
    assert len(client.tables["staff_profiles"]) == 1 and client.tables["staff_profiles"][0]["consent_at"] == first
    profiles.update_self("lan@truongvietanh.com", {"phone": "0901"})                # đã có dòng: vẫn giữ mốc cũ
    assert profiles.give_consent("lan@truongvietanh.com") == first


def test_self_update_audit_records_field_names_only_never_values():
    _, _, client = make()
    profiles = ProfileService(client)
    profiles.update_self("lan@truongvietanh.com", {"phone": "0901234567", "address": "Số 1 Đường bí mật", "bio": ""})
    log = client.tables["profile_audit_log"]
    assert len(log) == 1 and log[0]["action"] == "self_update" and log[0]["actor"] == "lan@truongvietanh.com"
    assert sorted(log[0]["changes"]["fields"]) == ["address", "phone"] and list(log[0]["changes"]) == ["fields"]
    assert "0901234567" not in str(log) and "bí mật" not in str(log)
    profiles.update_self("lan@truongvietanh.com", {"phone": "0901234567"})           # không đổi: không ghi thêm
    assert len(client.tables["profile_audit_log"]) == 1


def test_profile_exposes_new_hr_fields_requestable_fields_and_own_requests():
    directory, requests, client = make()
    directory.update("lan@truongvietanh.com", {"employment_type": "co_huu", "team_name": "Tổ Toán", "subjects": "Toán", "grade_levels": "Khối 6", "extra_roles": "Chủ nhiệm 6A"})
    requests.create("lan@truongvietanh.com", "campus", "Thái Sơn")
    requests.create("mgr@truongvietanh.com", "campus", "Nhân Lễ")
    profile = ProfileService(client).get("lan@truongvietanh.com")
    assert (profile["employment_type"], profile["employment_type_label"], profile["team_name"], profile["subjects"]) == ("co_huu", "Cơ hữu", "Tổ Toán", "Toán")
    assert (profile["grade_levels"], profile["extra_roles"], profile["consent_at"]) == ("Khối 6", "Chủ nhiệm 6A", None)
    assert [r["field"] for r in profile["requests"]] == ["campus"] and profile["requests"][0]["label"] == "Cơ sở"
    assert {"field": "work_phone", "label": "Điện thoại công việc", "max_length": 30, "type": "text"} in profile["requestable_fields"]
    assert {f["field"]: f["type"] for f in profile["requestable_fields"]}["employment_start_date"] == "date"


# ---------- HTTP ----------
@pytest.fixture
def http():
    directory, requests, client = make()
    rag = SimpleNamespace(directory=directory, change_requests=requests, profiles=ProfileService(client), points=PointsService(client), chat=None)
    state = {"email": "lan@truongvietanh.com"}
    app.dependency_overrides[get_rag_service] = lambda: rag
    app.dependency_overrides[get_settings] = lambda: SimpleNamespace(admin_token="admin-secret")
    app.dependency_overrides[require_user] = lambda: {"id": "u1", "email": state["email"], "user_metadata": {}}
    try:
        yield TestClient(app), state, client
    finally:
        app.dependency_overrides.clear()


def test_api_admin_routes_require_the_admin_token(http):
    api, _, _ = http
    request_id = str(uuid.uuid4())
    calls = [("get", "/api/admin/directory", None), ("post", "/api/admin/directory", {"email": "a@truongvietanh.com"}),
             ("put", "/api/admin/directory/lan@truongvietanh.com", {"campus": "x"}), ("post", "/api/admin/directory/lan@truongvietanh.com/deactivate", {}),
             ("post", "/api/admin/directory/lan@truongvietanh.com/reactivate", None), ("post", "/api/admin/directory/import", {"rows": []}),
             ("get", "/api/admin/directory/lan@truongvietanh.com/history", None), ("get", "/api/admin/change-requests", None),
             ("post", f"/api/admin/change-requests/{request_id}/approve", {}), ("post", f"/api/admin/change-requests/{request_id}/reject", {"note": "x"})]
    for method, path, body in calls:
        assert api.request(method.upper(), path, json=body).status_code == 401, path
        assert api.request(method.upper(), path, json=body, headers={"X-Admin-Token": "sai"}).status_code == 401, path


def test_api_directory_admin_flow(http):
    api, _, client = http
    created = api.post("/api/admin/directory", headers=ADMIN, json={"email": "Moi@truongvietanh.com", "display_name": "Người Mới", "department": "IT", "job_title": "Dev"})
    assert created.status_code == 200 and created.json()["email"] == "moi@truongvietanh.com"
    assert api.post("/api/admin/directory", headers=ADMIN, json={"email": "moi@truongvietanh.com", "display_name": "x", "department": "x", "job_title": "x"}).status_code == 409
    assert api.post("/api/admin/directory", headers=ADMIN, json={"email": "z@gmail.com", "display_name": "x", "department": "x", "job_title": "x"}).status_code == 422
    assert api.put("/api/admin/directory/moi@truongvietanh.com", headers=ADMIN, json={"team_name": "Tổ IT"}).json()["team_name"] == "Tổ IT"
    assert api.put("/api/admin/directory/moi@truongvietanh.com", headers=ADMIN, json={"email": "khac@truongvietanh.com"}).status_code == 422        # không đổi email
    assert api.put("/api/admin/directory/moi@truongvietanh.com", headers=ADMIN, json={"manager_email": "moi@truongvietanh.com"}).status_code == 422
    assert api.put("/api/admin/directory/ghost@truongvietanh.com", headers=ADMIN, json={"campus": "x"}).status_code == 404
    assert api.post("/api/admin/directory/moi@truongvietanh.com/deactivate", headers=ADMIN, json={"end_date": "2026-10-01"}).json()["active"] is False
    assert api.post("/api/admin/directory/moi@truongvietanh.com/deactivate", headers=ADMIN).status_code == 409
    assert api.post("/api/admin/directory/moi@truongvietanh.com/reactivate", headers=ADMIN).json()["active"] is True
    listing = api.get("/api/admin/directory", headers=ADMIN, params={"q": "nguoi moi"}).json()
    assert listing["total"] == 1 and listing["items"][0]["email"] == "moi@truongvietanh.com"
    history = api.get("/api/admin/directory/moi@truongvietanh.com/history", headers=ADMIN).json()["items"]
    assert {h["action"] for h in history} == {"create", "hr_update", "deactivate", "reactivate"}
    dry = api.post("/api/admin/directory/import", headers=ADMIN, json={"rows": [{"email": "n@truongvietanh.com", "display_name": "N", "department": "IT", "job_title": "Dev"}]})
    assert dry.json()["dry_run"] is True and dry.json()["created"] == 1 and len(client.tables["employee_directory"]) == 4       # mặc định dry_run
    assert api.post("/api/admin/directory/import", headers=ADMIN, json={"rows": [{"email": "n@truongvietanh.com", "display_name": "N", "department": "IT", "job_title": "Dev"}], "dry_run": False}).json()["created"] == 1
    assert len(client.tables["employee_directory"]) == 5
    assert api.post("/api/admin/directory/import", headers=ADMIN, json={"rows": [{}] * 1001}).status_code == 422


def test_api_change_request_flow_end_to_end(http):
    api, state, client = http
    assert api.post("/api/me/profile/consent").json()["status"] == "saved"
    created = api.post("/api/me/change-requests", json={"field": "campus", "new_value": "Thái Sơn", "reason": "Điều chuyển"})
    assert created.status_code == 200 and created.json()["label"] == "Cơ sở" and created.json()["status"] == "pending"
    request_id = created.json()["id"]
    assert api.post("/api/me/change-requests", json={"field": "campus", "new_value": "Nhân Lễ"}).status_code == 409
    assert api.post("/api/me/change-requests", json={"field": "employee_code", "new_value": "NV9"}).status_code == 422
    assert api.post("/api/me/change-requests", json={"field": "campus", "new_value": "Việt Anh"}).status_code == 422
    assert api.get("/api/me/change-requests").json()["items"][0]["id"] == request_id
    assert api.get("/api/me/profile").json()["requests"][0]["id"] == request_id

    state["email"] = "mgr@truongvietanh.com"                                                  # người khác không huỷ được
    assert api.post(f"/api/me/change-requests/{request_id}/cancel").status_code == 403
    state["email"] = "ghost@truongvietanh.com"
    assert api.post("/api/me/change-requests", json={"field": "campus", "new_value": "x"}).status_code == 404

    pending = api.get("/api/admin/change-requests", headers=ADMIN).json()
    assert pending["pending_count"] == 1 and pending["items"][0]["display_name"] == "Nguyễn Thị Lan"
    approved = api.post(f"/api/admin/change-requests/{request_id}/approve", headers=ADMIN, json={"note": "OK"})
    assert approved.status_code == 200 and approved.json()["status"] == "approved"
    assert client.tables["employee_directory"][0]["campus"] == "Thái Sơn"
    assert api.post(f"/api/admin/change-requests/{request_id}/approve", headers=ADMIN).status_code == 409

    state["email"] = "lan@truongvietanh.com"
    second = api.post("/api/me/change-requests", json={"field": "work_phone", "new_value": "0900"}).json()
    assert api.post(f"/api/admin/change-requests/{second['id']}/reject", headers=ADMIN, json={}).status_code == 422                 # thiếu lý do
    assert api.post(f"/api/admin/change-requests/{second['id']}/reject", headers=ADMIN, json={"note": "Sai số"}).json()["status"] == "rejected"
    third = api.post("/api/me/change-requests", json={"field": "work_phone", "new_value": "0911"}).json()
    assert api.post(f"/api/me/change-requests/{third['id']}/cancel").json() == {"status": "cancelled"}
    assert api.get("/api/admin/change-requests", headers=ADMIN, params={"status": "all"}).json()["pending_count"] == 0
    assert api.get("/api/admin/change-requests", headers=ADMIN, params={"status": "bogus"}).status_code == 422


def test_api_consent_and_self_update_audit(http):
    api, _, client = http
    first = api.post("/api/me/profile/consent").json()["consent_at"]
    assert api.post("/api/me/profile/consent").json()["consent_at"] == first
    assert api.get("/api/me/profile").json()["consent_at"] == first
    assert api.put("/api/me/profile", json={"phone": "0901", "bio": "Xin chào"}).status_code == 200
    log = client.tables["profile_audit_log"]
    assert len(log) == 1 and sorted(log[0]["changes"]["fields"]) == ["bio", "phone"] and "0901" not in str(log)


def test_missing_migration_gives_503_for_staff_and_500_for_admin_with_the_migration_name(http):
    api, _, _ = http

    class Broken:
        def select(self, *a, **k):
            raise RuntimeError('relation "profile_change_requests" does not exist')

    directory = DirectoryService(Broken())
    app.dependency_overrides[get_rag_service] = lambda: SimpleNamespace(
        directory=directory, change_requests=ChangeRequestService(Broken(), directory), profiles=ProfileService(Broken()), points=PointsService(Broken()))
    staff = api.get("/api/me/change-requests")
    assert staff.status_code == 503 and "20261010000000_directory_admin_and_change_requests.sql" in staff.json()["detail"]
    admin = api.get("/api/admin/directory", headers=ADMIN)
    assert admin.status_code == 500 and "20261010000000_directory_admin_and_change_requests.sql" in admin.json()["detail"]
