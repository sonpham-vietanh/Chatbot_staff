from app.rag.pipeline import AdvancedRAGPipeline


def test_parse_citations_handles_missing_closing_bracket():
    """Model đôi khi liệt kê nhiều nguồn liên tiếp mà quên đóng ']' ở dòng đầu — vẫn phải
    parse đúng theo từng dòng thay vì đòi hỏi regex khớp cả cặp ngoặc."""
    answer = (
        "Câu trả lời.\n"
        "[Nguồn: a.md > Heading A > 0.1\n"
        "[Nguồn: b.md > Heading B > Sub > 0.2]"
    )
    citations = AdvancedRAGPipeline._parse_citations(answer)
    assert citations == [
        {"source": "a.md", "heading": "Heading A", "version": "0.1"},
        {"source": "b.md", "heading": "Heading B", "version": "Sub > 0.2"},
    ]


def test_strip_citation_tags_removes_source_lines_only():
    answer = "Trả lời tự nhiên.\n\n[Nguồn: a.md > Heading > 0.1]"
    stripped = AdvancedRAGPipeline._strip_citation_tags(answer)
    assert stripped == "Trả lời tự nhiên."


def test_retrieval_query_prepends_last_user_turn_for_followups():
    history = [
        {"role": "user", "content": "Bậc lương tăng thế nào?"},
        {"role": "assistant", "content": "..."},
    ]
    query = AdvancedRAGPipeline._retrieval_query("Vậy từ bậc 1 lên bậc 4 mất bao lâu?", history)
    assert query.startswith("Bậc lương tăng thế nào?")
    assert "bậc 4" in query


def test_retrieval_query_falls_back_to_question_without_history():
    assert AdvancedRAGPipeline._retrieval_query("Câu hỏi", None) == "Câu hỏi"
