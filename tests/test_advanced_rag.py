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
        {"source": "b.md", "heading": "Heading B > Sub", "version": "0.2"},
    ]


def test_parse_citations_without_version_keeps_heading():
    citations = AdvancedRAGPipeline._parse_citations("Trả lời.\n[Nguồn: a.md > Heading A]")
    assert citations == [{"source": "a.md", "heading": "Heading A", "version": "unknown"}]


def test_parse_citations_does_not_mistake_last_heading_level_for_version():
    """LLM bỏ version, hoặc để trống sau dấu '>' cuối: phần cuối không có dạng số phiên bản
    thì vẫn là heading, version rơi về 'unknown' chứ không thành 'vĐiều 2' hay 'v' trơ trọi."""
    answer = "Trả lời.\n[Nguồn: a.md > Chương 1 > Điều 2]\n[Nguồn: b.md > Heading B > ]"
    assert AdvancedRAGPipeline._parse_citations(answer) == [
        {"source": "a.md", "heading": "Chương 1 > Điều 2", "version": "unknown"},
        {"source": "b.md", "heading": "Heading B", "version": "unknown"},
    ]


def test_parse_citations_prefers_metadata_of_retrieved_chunks():
    results = [{"metadata": {"source_file": "Quy định", "heading": "Quy định > Điều 2", "version": "0.1"}}]
    answer = "Trả lời.\n[Nguồn: Quy định > Quy định > Điều 2]\n[Nguồn: Quy định > Quy định > Điều 2 > 0.1]"
    assert AdvancedRAGPipeline._parse_citations(answer, results) == [
        {"source": "Quy định", "heading": "Quy định > Điều 2", "version": "0.1"},
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
