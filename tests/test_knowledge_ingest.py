from pathlib import Path

import frontmatter
import pytest

from app.services.knowledge_ingest import KnowledgeIngestService


def test_ingest_text_creates_draft_obsidian_note(tmp_path: Path):
    result = KnowledgeIngestService(tmp_path).ingest(
        "quy_trinh_moi.txt",
        "Bước một: gửi đề nghị cho HR.".encode("utf-8"),
        "HR",
    )
    note = frontmatter.load(result["note_path"])

    assert result["status"] == "draft"
    assert note["status"] == "draft"
    assert note["department"] == "HR"
    assert "Bước một" in note.content


def test_ingest_rejects_unsupported_file(tmp_path: Path):
    with pytest.raises(ValueError, match="Định dạng chưa được hỗ trợ"):
        KnowledgeIngestService(tmp_path).ingest("secret.exe", b"data", "HR")
