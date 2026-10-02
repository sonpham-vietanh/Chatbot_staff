import pytest
from pydantic import ValidationError

from app.models.schemas import MAX_HISTORY_ITEMS, MAX_TURN_CHARS, ChatRequest
from app.rag.chunking import chunk_content
from app.rag.embeddings import MockEmbeddingProvider


def test_chat_request_keeps_latest_history_and_truncates_long_turns():
    """Client (kể cả bên tích hợp widget) gửi cả cuộc trò chuyện dài hoặc câu trả lời cũ
    quá dài thì vẫn được nhận — chỉ giữ các lượt gần nhất, không trả 422."""
    history = [{"role": "user", "content": f"câu {index}"} for index in range(MAX_HISTORY_ITEMS + 5)]
    history.append({"role": "assistant", "content": "a" * (MAX_TURN_CHARS + 500)})

    request = ChatRequest(question="Câu hỏi nối tiếp", history=history)

    assert len(request.history) == MAX_HISTORY_ITEMS
    assert request.history[0].content == "câu 6"
    assert len(request.history[-1].content) == MAX_TURN_CHARS


def test_chat_request_still_rejects_malformed_input():
    with pytest.raises(ValidationError):
        ChatRequest(question="a")
    with pytest.raises(ValidationError):
        ChatRequest(question="Câu hỏi", history=[{"role": "bot", "content": "x"}])
    with pytest.raises(ValidationError):
        ChatRequest(question="Câu hỏi", history="không phải mảng")


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
