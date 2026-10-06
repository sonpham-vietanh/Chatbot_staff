"""Quản lý tri thức cho leader: quyền theo phòng ban, lịch sử sửa/khôi phục, liên kết [[...]]."""
import re
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient

from app.api.routes import get_rag_service, require_user, router as api_router
from app.config import get_settings
from app.main import app
from app.services.admin_service import NoteNotFoundError
from app.services.manage_service import LinkIndex, ManageError, ManageService, extract_links, slugify

ADMIN_HEADERS = {"X-Admin-Token": "admin-secret"}


class FakeAdmin:
    """Thay AdminService: bỏ qua embedding, chỉ giữ hành vi CRUD cần cho phân quyền/lịch sử."""

    def __init__(self, client):
        self.client = client

    def get_note(self, note_id):
        rows = [n for n in self.client.tables["knowledge_notes"] if n["id"] == note_id]
        if not rows:
            raise NoteNotFoundError(note_id)
        return dict(rows[0])

    def create_note(self, title, department, content, access_level="staff", status="draft", created_by="Admin", source_file=None):
        note = {"id": f"n{len(self.client.tables['knowledge_notes']) + 100}", "title": title, "department": department,
                "content": content, "access_level": access_level, "status": status, "created_by": created_by,
                "version": "0.1", "updated_at": "now", "source_file": source_file}
        self.client.tables["knowledge_notes"].append(note)
        return dict(note)

    def save_note(self, note_id, patch):
        for note in self.client.tables["knowledge_notes"]:
            if note["id"] == note_id:
                note.update(patch)
                return dict(note)
        raise NoteNotFoundError(note_id)

    def delete_note(self, note_id):
        self.client.tables["knowledge_notes"][:] = [n for n in self.client.tables["knowledge_notes"] if n["id"] != note_id]


class FakeClient:
    """Mô phỏng đủ PostgREST cho các truy vấn mà ManageService dùng."""

    def __init__(self):
        self.tables = {"knowledge_notes": [], "knowledge_leaders": [], "knowledge_note_versions": []}

    @staticmethod
    def _match(row, params):
        for key, value in params.items():
            if key in ("select", "order", "limit", "or"):
                continue
            if value.startswith("eq.") and str(row.get(key)).lower() != value[3:].lower():   # PostgREST: true/false viết thường
                return False
        return True

    def select(self, table, params):
        rows = [dict(r) for r in self.tables[table] if self._match(r, params)]
        if params.get("order", "").startswith("version_no.desc"):
            rows.sort(key=lambda r: -r["version_no"])
        if "or" in params:
            term = re.search(r"title\.ilike\.\*(.*?)\*,", params["or"]).group(1).lower()
            rows = [r for r in rows if term in r["title"].lower() or term in r["content"].lower()]
        return rows[: int(params.get("limit", 1000))]

    def insert(self, table, rows, returning=True):
        out = []
        for row in rows:
            row = {"id": f"v{len(self.tables[table]) + 1}", **row}
            self.tables[table].append(row)
            out.append(dict(row))
        return out if returning else None

    def update(self, table, params, patch):
        hit = [r for r in self.tables[table] if self._match(r, params)]
        for r in hit:
            r.update(patch)
        return [dict(r) for r in hit]

    def delete(self, table, params):
        self.tables[table][:] = [r for r in self.tables[table] if not self._match(r, params)]


def make_service(admin_emails="boss@truongvietanh.com"):
    client = FakeClient()
    notes = [
        ("n1", "Quy chế lương thưởng", "HR", "Xem [[khung-nang-luc-thang-luong]] và [[khong-co-trang-nay]]."),
        ("n2", "Khung năng lực & thang lương", "HR", "Liên quan [[quy-che-luong-thuong]]."),
        ("n3", "Quy trình thanh toán", "Finance", "Công tác phí."),
        ("n4", "Ghi chú chung", "Unassigned", "Nội dung chung."),
    ]
    client.tables["knowledge_notes"] = [
        {"id": i, "title": t, "department": d, "content": c, "status": "approved", "access_level": "staff",
         "version": "0.1", "updated_at": "now", "created_by": "Admin", "source_file": None} for i, t, d, c in notes]
    client.tables["knowledge_leaders"] = [
        {"email": "hr@truongvietanh.com", "display_name": "Leader HR", "departments": ["HR"], "active": True},
        {"email": "off@truongvietanh.com", "display_name": "Đã nghỉ", "departments": ["HR"], "active": False},
    ]
    return ManageService(FakeAdmin(client), client, admin_emails), client


def test_profiles_for_admin_leader_inactive_leader_and_stranger():
    service, _ = make_service()
    assert service.profile("BOSS@truongvietanh.com")["is_admin"] is True
    assert set(service.profile("boss@truongvietanh.com")["departments"]) == {"HR", "Finance", "Academic", "Admin", "Unassigned"}
    assert service.profile("hr@truongvietanh.com") == {"email": "hr@truongvietanh.com", "is_admin": False,
                                                       "departments": ["HR"], "can_manage": True}
    assert service.profile("off@truongvietanh.com")["can_manage"] is False
    assert service.profile("nobody@truongvietanh.com")["can_manage"] is False
    assert service.profile(None)["can_manage"] is False


def test_leader_edits_only_own_department_and_everything_is_logged():
    service, client = make_service()
    leader = service.profile("hr@truongvietanh.com")

    editable = {n["id"]: n["editable"] for n in service.list_notes(leader)}
    assert editable == {"n1": True, "n2": True, "n3": False, "n4": False}

    service.update_note(leader, "n1", {"content": "Mỗi năm 14 ngày phép."})
    assert client.tables["knowledge_notes"][0]["content"] == "Mỗi năm 14 ngày phép."
    assert client.tables["knowledge_notes"][0]["reviewed_by"] == "hr@truongvietanh.com"
    assert client.tables["knowledge_note_versions"][-1]["change_kind"] == "update"
    assert client.tables["knowledge_note_versions"][-1]["changed_by"] == "hr@truongvietanh.com"

    with pytest.raises(ManageError) as other_dept:
        service.update_note(leader, "n3", {"content": "x"})
    assert other_dept.value.status_code == 403
    with pytest.raises(ManageError):
        service.update_note(leader, "n1", {"department": "Finance"})      # không chuyển sang phòng khác
    with pytest.raises(ManageError):
        service.create_note(leader, "Mới", "Finance", "nội dung")
    with pytest.raises(ManageError):
        service.delete_note(leader, "n4")                                    # note Unassigned chỉ admin
    stranger = service.profile("nobody@truongvietanh.com")
    with pytest.raises(ManageError) as denied:
        service.list_notes(stranger)
    assert denied.value.status_code == 403


def test_admin_can_edit_every_department_including_unassigned():
    service, _ = make_service()
    admin = service.profile("boss@truongvietanh.com")
    service.update_note(admin, "n4", {"title": "Ghi chú chung (đã sửa)"})
    service.update_note(admin, "n3", {"department": "HR"})


def test_create_delete_and_restore_after_delete_keeps_history():
    service, client = make_service()
    leader = service.profile("hr@truongvietanh.com")

    note = service.create_note(leader, "Quy định mới", "HR", "Bản 1")
    service.update_note(leader, note["id"], {"content": "Bản 2"})
    service.delete_note(leader, note["id"])
    assert all(n["id"] != note["id"] for n in client.tables["knowledge_notes"])
    assert [v["change_kind"] for v in client.tables["knowledge_note_versions"]] == ["create", "update", "delete"]

    first = client.tables["knowledge_note_versions"][0]
    restored = service.restore(leader, note["id"], first["id"])         # note đã xoá -> tạo lại từ bản lưu
    assert restored["content"] == "Bản 1"
    assert {v["note_id"] for v in client.tables["knowledge_note_versions"]} == {restored["id"]}
    assert client.tables["knowledge_note_versions"][-1]["change_kind"] == "restore"
    assert service.versions(leader, restored["id"])[0]["version_no"] == 4

    with pytest.raises(ManageError) as missing:
        service.restore(leader, restored["id"], "no-such-version")
    assert missing.value.status_code == 404


def test_content_limits_and_blank_input_rejected():
    service, _ = make_service()
    leader = service.profile("hr@truongvietanh.com")
    with pytest.raises(ManageError):
        service.update_note(leader, "n1", {"content": "   "})
    with pytest.raises(ManageError):
        service.create_note(leader, "  ", "HR", "x")
    with pytest.raises(ManageError):
        service.create_note(leader, "Dài", "HR", "x" * 100_001)


def test_links_resolve_by_slug_title_and_source_file_with_backlinks():
    service, client = make_service()
    client.tables["knowledge_notes"].append({
        "id": "n5", "title": "Nguồn: Quy chế lương thưởng HR01", "department": "HR", "content": "Tóm tắt nguồn.",
        "status": "approved", "access_level": "staff", "version": "0.1", "updated_at": "now", "created_by": "WikiSync",
        "source_file": "vault:nhan-su/wiki/sources/quy-che-luong-thuong-hr01-nguon.md"})
    client.tables["knowledge_notes"][3]["content"] += " Xem [[quy-che-luong-thuong-hr01-nguon]]."
    leader = service.profile("hr@truongvietanh.com")

    note = service.get_note(leader, "n1")
    assert [l["id"] for l in note["links"]["outgoing"]] == ["n2"]
    assert note["links"]["unresolved"] == ["khong-co-trang-nay"]
    assert [l["id"] for l in note["links"]["incoming"]] == ["n2"]
    assert [l["id"] for l in service.get_note(leader, "n5")["links"]["incoming"]] == ["n4"]


def test_link_helpers():
    assert slugify("Khung năng lực & thang lương Đỗ") == "khung-nang-luc-thang-luong-do"
    assert extract_links("[[a]] [[b|Tên hiển thị]] [[c#mục]] [[a]]") == ["a", "b", "c"]
    index = LinkIndex([{"id": "1", "title": "Quy chế lương", "source_file": None}])
    assert index.resolve("quy-che-luong")["id"] == "1" and index.resolve("Quy chế lương")["id"] == "1"


@pytest.fixture
def api():
    service, client = make_service()
    rag = SimpleNamespace(manage=service, auth=None, supabase=None)
    current = {"email": "hr@truongvietanh.com"}
    app.dependency_overrides[get_rag_service] = lambda: rag
    app.dependency_overrides[get_settings] = lambda: SimpleNamespace(admin_token="admin-secret")
    app.dependency_overrides[require_user] = lambda: {"id": "u", "email": current["email"]}
    try:
        yield TestClient(app), client, current
    finally:
        app.dependency_overrides.clear()


def test_api_flow_for_a_leader(api):
    http, client, _ = api
    assert http.get("/api/manage/me").json()["departments"] == ["HR"]
    notes = http.get("/api/manage/notes").json()
    assert {n["id"]: n["editable"] for n in notes}["n3"] is False
    assert [n["id"] for n in http.get("/api/manage/notes?q=công tác").json()] == ["n3"]

    created = http.post("/api/manage/notes", json={"title": "Quy định mới", "department": "HR", "content": "Nội dung"})
    assert created.status_code == 200
    note_id = created.json()["id"]
    assert http.put(f"/api/manage/notes/{note_id}", json={"content": "Nội dung 2"}).status_code == 200
    versions = http.get(f"/api/manage/notes/{note_id}/versions").json()
    assert [v["change_kind"] for v in versions] == ["update", "create"]
    assert http.post(f"/api/manage/notes/{note_id}/restore/khong-phai-uuid").status_code == 422
    assert http.delete(f"/api/manage/notes/{note_id}").json() == {"status": "deleted"}
    assert http.get(f"/api/manage/notes/{note_id}").status_code == 404

    assert http.put("/api/manage/notes/n3", json={"content": "sửa trộm"}).status_code == 403
    assert http.post("/api/manage/notes", json={"title": "x", "department": "Finance", "content": "y"}).status_code == 403
    assert http.post("/api/manage/notes", json={"title": "x", "department": "Khác", "content": "y"}).status_code == 422


def test_api_denies_non_leaders_and_inactive_leaders(api):
    http, _, current = api
    for email in ("nobody@truongvietanh.com", "off@truongvietanh.com"):
        current["email"] = email
        assert http.get("/api/manage/me").json()["can_manage"] is False
        assert http.get("/api/manage/notes").status_code == 403
        assert http.get("/api/manage/notes/n1").status_code == 403
        assert http.delete("/api/manage/notes/n1").status_code == 403


def test_admin_manages_leaders_with_token_only(api):
    http, client, _ = api
    assert http.get("/api/admin/leaders").status_code == 401
    saved = http.put("/api/admin/leaders", headers=ADMIN_HEADERS, json={
        "email": " NEW@TruongVietAnh.com ", "display_name": "Leader tài chính", "departments": ["Finance", "Finance"]})
    assert saved.status_code == 200 and saved.json()["email"] == "new@truongvietanh.com"
    assert saved.json()["departments"] == ["Finance"]
    assert http.put("/api/admin/leaders", headers=ADMIN_HEADERS, json={"email": "a@x.com", "departments": ["Kho"]}).status_code == 422
    assert http.put("/api/admin/leaders", headers=ADMIN_HEADERS, json={"email": "khong-co-a-cong", "departments": []}).status_code == 422
    assert any(l["email"] == "new@truongvietanh.com" for l in http.get("/api/admin/leaders", headers=ADMIN_HEADERS).json())
    assert http.delete("/api/admin/leaders?email=new@truongvietanh.com", headers=ADMIN_HEADERS).status_code == 200
    assert not any(l["email"] == "new@truongvietanh.com" for l in client.tables["knowledge_leaders"])


def test_every_admin_route_rejects_requests_without_the_admin_token(api):
    http, _, _ = api
    admin_routes = [r for r in api_router.routes if getattr(r, "path", "").startswith("/api/admin/")]
    assert len(admin_routes) > 10  # phòng khi ai đó đổi prefix làm test này rỗng
    for route in admin_routes:
        path = route.path.replace("{note_id}", "n1").replace("{feedback_id}", "f1").replace("{key_id}", "k1").replace("{version_id}", "v1")
        path = path.replace("{", "x").replace("}", "")
        for method in route.methods - {"HEAD", "OPTIONS"}:
            assert http.request(method, path).status_code in (401, 503), f"{method} {route.path} không yêu cầu admin token"


def test_stale_edit_is_rejected_instead_of_silently_overwriting(api):
    http, client, _ = api
    note_id = http.post("/api/manage/notes", json={"title": "Quy định X", "department": "HR", "content": "bản 1"}).json()["id"]
    fresh = http.get(f"/api/manage/notes/{note_id}").json()["updated_at"]

    stale = http.put(f"/api/manage/notes/{note_id}", json={"content": "bản của người chậm", "base_updated_at": "thoi-diem-cu"})
    assert stale.status_code == 409 and "tải lại" in stale.json()["detail"].lower()
    assert http.get(f"/api/manage/notes/{note_id}").json()["content"] == "bản 1"

    assert http.put(f"/api/manage/notes/{note_id}", json={"content": "bản 2", "base_updated_at": fresh}).status_code == 200
    assert http.put(f"/api/manage/notes/{note_id}", json={"content": "bản 3"}).status_code == 200  # gọi API không kèm mốc vẫn dùng được
