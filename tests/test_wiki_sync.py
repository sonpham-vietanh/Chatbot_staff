"""Đồng bộ wiki -> knowledge_notes: chỉ cập nhật note gốc từ wiki, không bao giờ xoá note
tạo trong /admin hay note [CẦN BỔ SUNG] (bản cũ xoá sạch rồi tạo lại toàn bộ)."""
from app.services.wiki_sync_service import WikiSyncService


class FakeAdmin:
    def __init__(self, notes):
        self.notes = {n["id"]: dict(n) for n in notes}
        self.next_id = 100
        self.saved, self.deleted = [], []

    def list_notes(self, status=None):
        return [dict(n) for n in self.notes.values()]

    def create_note(self, title, department, content, access_level="staff", status="draft", created_by="Admin", source_file=None):
        self.next_id += 1
        note = {"id": str(self.next_id), "title": title, "department": department, "content": content,
                "access_level": access_level, "status": status, "created_by": created_by, "source_file": source_file}
        self.notes[note["id"]] = note
        return note

    def save_note(self, note_id, patch):
        self.saved.append((note_id, patch))
        self.notes[note_id].update(patch)
        return dict(self.notes[note_id])

    def delete_note(self, note_id):
        self.deleted.append(note_id)
        self.notes.pop(note_id)


def write_page(vault, rel, title, body, confidential=False):
    path = vault / rel
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(f"---\ntitle: \"{title}\"\nconfidential: {str(confidential).lower()}\n---\n\n{body}\n", encoding="utf-8")


def test_sync_updates_wiki_notes_and_never_touches_admin_or_gap_notes(tmp_path):
    vault = tmp_path / "vault"
    write_page(vault, "nhan-su/wiki/concepts/nghi-phep.md", "Quy định nghỉ phép", "Mỗi năm 14 ngày phép.")
    write_page(vault, "nhan-su/wiki/concepts/moi.md", "Trang mới", "Nội dung mới.")
    admin = FakeAdmin([
        # trang wiki đã đẩy lên từ trước (đời cũ: created_by Admin, chưa có source_file)
        {"id": "1", "title": "Quy định nghỉ phép", "department": "HR", "content": "Mỗi năm 12 ngày phép.",
         "access_level": "staff", "status": "approved", "created_by": "Admin", "source_file": None},
        {"id": "2", "title": "[CẦN BỔ SUNG] Phụ cấp xăng xe?", "department": "Unassigned", "content": "...",
         "access_level": "staff", "status": "draft", "created_by": "AI_Bot", "source_file": None},
        {"id": "3", "title": "Ghi chú HR tự viết", "department": "HR", "content": "Do admin tạo.",
         "access_level": "staff", "status": "approved", "created_by": "Admin", "source_file": None},
        {"id": "4", "title": "Upload", "department": "HR", "content": "từ file", "access_level": "staff",
         "status": "approved", "created_by": "Admin", "source_file": "quy-che.docx"},
        # trang wiki đã bị xoá khỏi vault
        {"id": "5", "title": "Trang cũ", "department": "HR", "content": "cũ", "access_level": "staff",
         "status": "approved", "created_by": "WikiSync", "source_file": "vault:nhan-su/wiki/concepts/cu.md"},
    ])

    result = WikiSyncService(admin, str(vault)).sync_all()

    assert result == {"created": 1, "updated": 1, "unchanged": 0, "deleted": 1, "failed": 0, "total": 2}
    assert admin.deleted == ["5"]
    assert {"2", "3", "4"} <= set(admin.notes)            # note admin / gap / upload còn nguyên
    assert admin.notes["3"]["content"] == "Do admin tạo."
    updated = admin.notes["1"]
    assert updated["content"] == "Mỗi năm 14 ngày phép."
    assert updated["source_file"] == "vault:nhan-su/wiki/concepts/nghi-phep.md"
    assert updated["status"] == "approved"                 # trạng thái duyệt giữ nguyên

    # Lần đồng bộ thứ hai: không có gì đổi -> không ghi/không embed lại
    admin.saved.clear()
    again = WikiSyncService(admin, str(vault)).sync_all()
    assert again["unchanged"] == 2 and again["created"] == 0 and again["deleted"] == 0
    assert admin.saved == []


def test_admin_rejected_wiki_note_keeps_its_status_after_content_change(tmp_path):
    vault = tmp_path / "vault"
    write_page(vault, "nhan-su/wiki/a.md", "Trang A", "nội dung mới")
    admin = FakeAdmin([{"id": "1", "title": "Trang A", "department": "HR", "content": "cũ", "access_level": "staff",
                        "status": "rejected", "created_by": "WikiSync", "source_file": "vault:nhan-su/wiki/a.md"}])

    WikiSyncService(admin, str(vault)).sync_all()

    assert admin.notes["1"]["status"] == "rejected"
    assert "status" not in admin.saved[0][1]


def test_pages_marked_chatbot_false_or_with_broken_frontmatter_are_skipped_and_removed(tmp_path):
    from app.services.wiki_sync_service import collect_wiki_pages

    vault = tmp_path / "vault"
    write_page(vault, "nhan-su/wiki/concepts/giu.md", "Trang giữ", "Nội dung.")
    hidden = vault / "core" / "concepts" / "huong-dan.md"
    hidden.parent.mkdir(parents=True, exist_ok=True)
    hidden.write_text('---\ntitle: "Hướng dẫn nội bộ"\nchatbot: false\n---\n\nKhông cho chatbot.\n', encoding="utf-8")
    broken = vault / "nhan-su" / "wiki" / "concepts" / "hong.md"
    broken.write_text('---\ntitle: "Hỏng: [\n  - x: : :\n---\n\nbody\n', encoding="utf-8")

    assert [p["title"] for p in collect_wiki_pages(str(vault))] == ["Trang giữ"]

    # Trang đã lỡ đồng bộ trước đó rồi mới được đánh dấu chatbot:false -> bị gỡ khỏi chatbot.
    admin = FakeAdmin([{"id": "1", "title": "Hướng dẫn nội bộ", "department": "Unassigned", "content": "x", "access_level": "staff",
                        "status": "approved", "created_by": "WikiSync", "source_file": "vault:core/concepts/huong-dan.md"}])
    result = WikiSyncService(admin, str(vault)).sync_all()
    assert admin.deleted == ["1"] and result["deleted"] == 1


def test_sync_records_history_so_a_leader_edit_overwritten_by_obsidian_is_restorable(tmp_path):
    vault = tmp_path / "vault"
    write_page(vault, "nhan-su/wiki/a.md", "Trang A", "bản Obsidian")
    write_page(vault, "nhan-su/wiki/b.md", "Trang B", "mới tinh")
    admin = FakeAdmin([{"id": "1", "title": "Trang A", "department": "HR", "content": "bản leader sửa trên web", "access_level": "staff",
                        "status": "approved", "created_by": "WikiSync", "source_file": "vault:nhan-su/wiki/a.md"}])
    recorded = []

    WikiSyncService(admin, str(vault), recorder=lambda note, kind: recorded.append((note["title"], note["content"], kind))).sync_all()

    assert sorted(recorded) == [("Trang A", "bản Obsidian", "sync"), ("Trang B", "mới tinh", "sync")]


def test_history_failure_never_breaks_the_sync(tmp_path):
    vault = tmp_path / "vault"
    write_page(vault, "nhan-su/wiki/a.md", "Trang A", "nội dung")

    def boom(note, kind):
        raise RuntimeError("db down")

    result = WikiSyncService(FakeAdmin([]), str(vault), recorder=boom).sync_all()
    assert result["created"] == 1 and result["failed"] == 0
