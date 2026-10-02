import pytest

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


BODY = "Bạn cần báo trước **48 giờ**."
TAG = "[Nguồn: Quy định nghỉ phép > Quy định > Quy trình > 0.1]"
CITED = [{"source": "Quy định nghỉ phép", "heading": "Quy định > Quy trình", "version": "0.1"}]


@pytest.mark.parametrize("answer", [
    f"{BODY}\n\n{TAG}",
    f"{BODY} {TAG}",                                    # thẻ ở cuối câu
    f"{BODY}\n\n- {TAG}",                               # thẻ trong bullet
    f"{BODY}\n\n1. {TAG}",
    f"{BODY}\n\n**{TAG}**",                             # thẻ bọc đậm
    f"{BODY}\n\n`{TAG}`",
    f"{BODY}\n\n{TAG}.",
    f"{BODY}\n\n{TAG.replace('[Nguồn:', '[nguồn:')}",   # viết thường
    f"{BODY}\n\n{TAG.replace('[Nguồn:', '[ Nguồn :')}",
    f"{BODY}\n\n{TAG.replace('[Nguồn:', '[**Nguồn:**')}",
    f"{BODY}\n\n{TAG[:-1]}",                            # thiếu ]
    f"{BODY}\n\n**Nguồn:**\n{TAG}",                     # nhãn giới thiệu đứng trơ trọi
    f"{BODY}\n\nCác nguồn đã dùng:\n- {TAG}",
])
def test_citation_tag_variants_never_leak_into_the_answer(answer):
    assert AdvancedRAGPipeline._strip_citation_tags(answer) == BODY
    assert AdvancedRAGPipeline._parse_citations(answer) == CITED


def test_text_sharing_a_line_with_a_citation_tag_is_kept():
    """Gỡ đúng phần thẻ — câu chữ thật cùng dòng không được biến mất theo."""
    assert AdvancedRAGPipeline._strip_citation_tags(f"{TAG} quy định bạn cần báo trước 48 giờ.") == (
        "quy định bạn cần báo trước 48 giờ."
    )
    assert AdvancedRAGPipeline._strip_citation_tags(f"Theo quy định {TAG}, bạn cần báo trước 48 giờ.") == (
        "Theo quy định, bạn cần báo trước 48 giờ."
    )
    assert AdvancedRAGPipeline._strip_citation_tags(f"Bạn được nghỉ 12 ngày {TAG}.") == "Bạn được nghỉ 12 ngày."
    assert AdvancedRAGPipeline._strip_citation_tags(f"[Nguồn:\n\n{BODY}") == BODY
    assert AdvancedRAGPipeline._strip_citation_tags(f"{BODY}\n\n[Nguồn") == BODY  # thẻ bị cắt dở do hết token
    # Ngoặc vuông khác trên cùng dòng không thuộc về thẻ
    assert AdvancedRAGPipeline._strip_citation_tags("Theo [Nguồn: HR03 > Nghỉ phép > 2.0], xem thêm [Phụ lục 2]") == (
        "Theo, xem thêm [Phụ lục 2]"
    )
    assert AdvancedRAGPipeline._strip_citation_tags("Bạn cần [Nguồn: HR03 > Mục [1] > 2.0] báo trước 48 giờ.") == (
        "Bạn cần báo trước 48 giờ."
    )
    # Chỉ đụng quanh thẻ: thụt lề của ý con và khoảng trắng chỗ khác giữ nguyên
    assert AdvancedRAGPipeline._strip_citation_tags(f"- Mục a {TAG}\n    - mục con  một {TAG}") == (
        "- Mục a\n    - mục con  một"
    )
    assert AdvancedRAGPipeline._strip_citation_tags(f"{BODY}\n\n([Nguồn: a > b > 1.0])") == BODY
    assert AdvancedRAGPipeline._strip_citation_tags(f"Bạn cần báo trước **48 giờ** ({TAG}).") == BODY
    # Nhãn "Nguồn:" đứng ngay trên dòng thẻ bị bỏ theo, kể cả khi sau đó còn câu khác
    assert AdvancedRAGPipeline._strip_citation_tags(f"{BODY}\n\nNguồn:\n{TAG}\n\nBạn cần hỗ trợ gì thêm không?") == (
        f"{BODY}\n\nBạn cần hỗ trợ gì thêm không?"
    )
    # Dòng cuối kết thúc bằng dấu hai chấm nhưng không có thẻ nguồn nào thì không bị coi là nhãn nguồn
    assert AdvancedRAGPipeline._strip_citation_tags("Thu nhập gồm lương.\nCác nguồn thu nhập khác:") == (
        "Thu nhập gồm lương.\nCác nguồn thu nhập khác:"
    )


def test_citation_tag_split_across_two_lines_is_still_parsed():
    answer = f"{BODY}\n\n[Nguồn:\nQuy định nghỉ phép > Quy định > Quy trình > 0.1]"

    assert AdvancedRAGPipeline._strip_citation_tags(answer) == BODY
    assert AdvancedRAGPipeline._parse_citations(answer) == CITED


def test_citation_handling_stays_fast_on_degenerate_input():
    """Bản regex cũ ("\\s*\\**\\s*" chồng nhau) treo 50 giây với dòng "[" + 500 dấu cách."""
    import time

    started = time.perf_counter()
    for answer in ("x\n[" + " " * 60000 + "y", "[" + "*" * 60000 + "y", "[ " * 20000,
                   "[Nguồn: a " * 5000, "x [Nguồn: a]" + " " * 60000 + "."):
        AdvancedRAGPipeline._strip_citation_tags(answer)
        AdvancedRAGPipeline._parse_citations(answer)

    assert time.perf_counter() - started < 2.0


def test_parse_citations_handles_several_sources_and_odd_shapes():
    two_on_one_line = f"{BODY}\n{TAG} [Nguồn: HR04 > Hỗ trợ ăn > v1.0]"
    merged = f"{BODY}\n[Nguồn: Quy định nghỉ phép > Quy định > Quy trình > 0.1; HR04 > Hỗ trợ ăn > 1.0]"
    expected = CITED + [{"source": "HR04", "heading": "Hỗ trợ ăn", "version": "1.0"}]

    assert AdvancedRAGPipeline._parse_citations(two_on_one_line) == expected
    assert AdvancedRAGPipeline._parse_citations(merged) == expected
    assert AdvancedRAGPipeline._parse_citations("x\n[Nguồn: ]\n[Nguồn:]") == []
    assert AdvancedRAGPipeline._parse_citations("x\n[Nguồn: **HR03** > Mục [1] > 0.2]") == [
        {"source": "HR03", "heading": "Mục [1]", "version": "0.2"},
    ]
    assert AdvancedRAGPipeline._parse_citations("x\n[Nguồn: Quy định nghỉ phép > 0.1]") == [
        {"source": "Quy định nghỉ phép", "heading": "Nội dung chung", "version": "0.1"},
    ]
    # Dấu ";" trong tên mục không phải ranh giới giữa 2 nguồn
    assert AdvancedRAGPipeline._parse_citations("x\n[Nguồn: HR03 > Lương; thưởng > Bậc 1 > 2.0]") == [
        {"source": "HR03", "heading": "Lương; thưởng > Bậc 1", "version": "2.0"},
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
