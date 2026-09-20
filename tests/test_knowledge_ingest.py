import io

import pytest
from docx import Document

from app.services.knowledge_ingest import KnowledgeIngestService


class FakeAdminService:
    def __init__(self):
        self.calls = []

    def create_note(self, title, department, content, status, created_by):
        note = {"id": "fake-id", "title": title, "department": department,
                "content": content, "status": status, "created_by": created_by}
        self.calls.append(note)
        return note


def test_ingest_text_creates_approved_note():
    admin = FakeAdminService()
    result = KnowledgeIngestService(admin).ingest(
        "quy_trinh_moi.txt",
        "Bước một: gửi đề nghị cho HR.".encode("utf-8"),
        "HR",
    )

    assert result["status"] == "approved"
    assert admin.calls[0]["status"] == "approved"
    assert admin.calls[0]["department"] == "HR"
    assert "Bước một" in admin.calls[0]["content"]


def test_ingest_docx_converts_word_headings_to_markdown():
    document = Document()
    document.add_paragraph("Nội quy công ty", style="Title")
    document.add_paragraph("Chương 1: Giờ làm việc", style="Heading 1")
    document.add_paragraph("Nhân viên làm việc 8 tiếng mỗi ngày.")
    document.add_paragraph("Mục 1.1: Ca sáng", style="Heading 2")
    document.add_paragraph("Bắt đầu từ 8h00.")
    buffer = io.BytesIO()
    document.save(buffer)

    admin = FakeAdminService()
    result = KnowledgeIngestService(admin).ingest("noi_quy.docx", buffer.getvalue(), "HR")

    content = admin.calls[0]["content"]
    assert "# Nội quy công ty" in content
    assert "## Chương 1: Giờ làm việc" in content
    assert "### Mục 1.1: Ca sáng" in content
    assert result["status"] == "approved"


def test_ingest_rejects_unsupported_file():
    with pytest.raises(ValueError, match="Định dạng chưa được hỗ trợ"):
        KnowledgeIngestService(FakeAdminService()).ingest("secret.exe", b"data", "HR")


def test_ingest_rejects_oversized_file():
    admin = FakeAdminService()
    oversized = b"x" * (10 * 1024 * 1024 + 1)
    with pytest.raises(ValueError, match="10 MB"):
        KnowledgeIngestService(admin).ingest("big.txt", oversized, "HR")
