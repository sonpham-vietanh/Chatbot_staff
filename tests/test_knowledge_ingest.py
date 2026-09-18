import pytest

from app.services.knowledge_ingest import KnowledgeIngestService


class FakeAdminService:
    def __init__(self):
        self.calls = []

    def create_note(self, title, department, content, status, created_by):
        note = {"id": "fake-id", "title": title, "department": department,
                "content": content, "status": status, "created_by": created_by}
        self.calls.append(note)
        return note


def test_ingest_text_creates_draft_note():
    admin = FakeAdminService()
    result = KnowledgeIngestService(admin).ingest(
        "quy_trinh_moi.txt",
        "Bước một: gửi đề nghị cho HR.".encode("utf-8"),
        "HR",
    )

    assert result["status"] == "draft"
    assert admin.calls[0]["status"] == "draft"
    assert admin.calls[0]["department"] == "HR"
    assert "Bước một" in admin.calls[0]["content"]


def test_ingest_rejects_unsupported_file():
    with pytest.raises(ValueError, match="Định dạng chưa được hỗ trợ"):
        KnowledgeIngestService(FakeAdminService()).ingest("secret.exe", b"data", "HR")


def test_ingest_rejects_oversized_file():
    admin = FakeAdminService()
    oversized = b"x" * (10 * 1024 * 1024 + 1)
    with pytest.raises(ValueError, match="10 MB"):
        KnowledgeIngestService(admin).ingest("big.txt", oversized, "HR")
