import json
from pathlib import Path

import frontmatter

from app.rag.vector_store import VectorStore
from app.services.draft_service import DraftService
from app.services.graph_service import GraphService
from app.services.vault_service import VaultService


def write_note(path: Path, title: str, department: str = "HR", access_level: str = "staff") -> None:
    path.write_text(
        f"""---
title: {title}
department: {department}
owner: hr@vietanh.edu.vn
status: approved
version: \"1.0\"
access_level: {access_level}
---

# {title}

Nội dung đã được phê duyệt.
""",
        encoding="utf-8",
    )


def test_build_where_ignores_access_level_content_is_transparent_to_all_chat_users():
    """Chat trả lời minh bạch cho mọi người dùng; access_level chỉ còn ý nghĩa thông tin,
    không hạn chế truy xuất nữa (chỉ admin panel mới cần token riêng để sửa/duyệt data)."""
    assert VectorStore._build_where({"user_access_level": "staff"}) == {"status": "approved"}
    assert VectorStore._build_where({"user_access_level": "admin"}) == {"status": "approved"}
    assert VectorStore._build_where({"user_department": "HR"}) == {
        "$and": [{"status": "approved"}, {"department": "HR"}]
    }


def test_graph_expands_approved_linked_note_without_duplicates(tmp_path: Path):
    vault = tmp_path / "vault"
    vault.mkdir()
    write_note(vault / "Root.md", "Root")
    write_note(vault / "Linked.md", "Linked", department="Finance")
    (vault / "Root.md").write_text(
        (vault / "Root.md").read_text(encoding="utf-8") + "\nXem [[Linked]].\n",
        encoding="utf-8",
    )
    graph_path = tmp_path / "graph.json"
    graph_path.write_text(json.dumps([{"source": "Root.md", "target": "Linked"}]), encoding="utf-8")
    vault_service = VaultService(vault, graph_path)
    graph_service = GraphService(graph_path, vault_service)
    seed = [{
        "id": "Root:1",
        "text": "Seed",
        "score": 0.9,
        "metadata": {"source_file": "Root.md", "source": "Root.md"},
    }]

    expanded = graph_service.expand_context(
        seed,
        {"user_access_level": "staff", "user_department": None, "version": None},
        max_chars=1000,
    )

    assert [item["id"] for item in expanded] == ["Root:1", "Linked:1"]
    assert expanded[1]["metadata"]["retrieval_stage"] == "graph_expansion"


def test_draft_has_obsidian_frontmatter(tmp_path: Path):
    path = DraftService(tmp_path).create_unanswered_draft("Quy trình mới là gì?", "Finance")
    note = frontmatter.load(path)

    assert note["title"] == "[CẦN BỔ SUNG] Quy trình mới là gì?"
    assert note["department"] == "Finance"
    assert note["owner"] == "hr@vietanh.edu.vn"
    assert note["status"] == "draft"
    assert note["created_by"] == "AI_Bot"
    assert note["version"] == "0.1"
    assert note["access_level"] == "staff"
