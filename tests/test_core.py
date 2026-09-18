from app.rag.chunking import chunk_content
from app.rag.embeddings import MockEmbeddingProvider


def test_mock_embedding_is_deterministic():
    provider = MockEmbeddingProvider(dimensions=16)
    assert provider.embed("nghỉ phép") == provider.embed("nghỉ phép")


def test_chunk_content_splits_by_heading():
    content = "# Tiêu đề\nGiới thiệu.\n\n## Mục 1\nNội dung 1.\n\n### Mục con\nChi tiết.\n"
    chunks = chunk_content(content)
    headings = [chunk["heading"] for chunk in chunks]
    assert headings == ["Tiêu đề", "Tiêu đề > Mục 1", "Tiêu đề > Mục 1 > Mục con"]
    assert "Chi tiết." in chunks[-1]["text"]


def test_chunk_content_splits_oversized_section():
    paragraph = "Câu dài lặp lại. " * 200  # ~3400 ký tự mỗi đoạn
    # 3 đoạn cách nhau blank line, tổng > 6000 ký tự -> phải tách thành nhiều chunk con
    content = f"# Điều 1\n\n{paragraph}\n\n{paragraph}\n\n{paragraph}"
    chunks = chunk_content(content)
    assert len(chunks) > 1
    assert all(chunk["heading"] == "Điều 1" for chunk in chunks)
    assert all(len(chunk["text"]) <= 6000 for chunk in chunks)
