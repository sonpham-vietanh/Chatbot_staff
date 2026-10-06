import pytest

from app.rag.answer_format import clean_markdown, to_plain_text
from app.rag.pipeline import AdvancedRAGPipeline
from app.rag.prompt_builder import FALLBACK_ANSWER


def test_plain_text_removes_bold_markers_but_keeps_bullets_and_line_breaks():
    answer = (
        "Bạn cần xin nghỉ phép tối thiểu **48 giờ** trước khi nghỉ.\n"
        "- Nếu là **Leaders**, email xin phép CEO.\n"
        "- Nhân viên gửi email cho **quản lý trực tiếp**."
    )

    assert to_plain_text(answer) == (
        "Bạn cần xin nghỉ phép tối thiểu 48 giờ trước khi nghỉ.\n\n"
        "- Nếu là Leaders, email xin phép CEO.\n"
        "- Nhân viên gửi email cho quản lý trực tiếp."
    )


def test_clean_markdown_keeps_only_bold_and_dash_bullets():
    answer = (
        "## Liên hệ\n\n\n"
        "Email `hr@truongvietanh.com`, *gấp* thì gọi __trưởng phòng__.\n"
        "* Ý một\n"
        "• Ý hai\n"
        "```\nmã\n```"
    )

    assert clean_markdown(answer) == (
        "Liên hệ\n\n"
        "Email hr@truongvietanh.com, gấp thì gọi **trưởng phòng**.\n\n"
        "- Ý một\n"
        "- Ý hai\n\n"
        "mã"
    )


def test_bullet_block_is_separated_from_plain_lines_by_a_blank_line():
    """Giao diện React chỉ dựng <ul> khi cả khối (ngăn bởi dòng trống) đều là bullet — câu
    dẫn dính liền danh sách sẽ hiện thành đoạn văn có dấu '-' thô (gặp ở ~16% câu trả lời thật)."""
    answer = "Các mức thưởng gồm:\n- **20/11**: 300.000đ\n  - Giáo viên cơ hữu\n- 8/3: quà 200.000đ\nÁp dụng từ 2024."

    assert clean_markdown(answer) == (
        "Các mức thưởng gồm:\n\n- **20/11**: 300.000đ\n  - Giáo viên cơ hữu\n- 8/3: quà 200.000đ\n\nÁp dụng từ 2024."
    )


@pytest.mark.parametrize("raw, expected", [
    ("Thưởng **5 triệu", "Thưởng 5 triệu"),                      # ** lẻ do câu bị cắt giữa chừng
    ("**Lưu ý:** áp dụng từ **2024", "**Lưu ý:** áp dụng từ 2024"),
    ("**** trống", "trống"),
    # 2 cụm đậm liền nhau: không được dính chữ (lỗi có trong dữ liệu thật)
    ("- **20/11:** **300.000đ/người**; giáo viên", "- **20/11:** **300.000đ/người**; giáo viên"),
    ("**Thứ Hai** **đến Thứ Sáu** làm việc", "**Thứ Hai** **đến Thứ Sáu** làm việc"),
    # Dấu sao / gạch dưới / cộng là NỘI DUNG, không phải Markdown
    ("Hệ số 2 * 3 * 4 và 5*3*2", "Hệ số 2 * 3 * 4 và 5*3*2"),
    ("Lương = (Lương cơ bản)*(Hệ số) + (Phụ cấp)*(Ngày công)", "Lương = (Lương cơ bản)*(Hệ số) + (Phụ cấp)*(Ngày công)"),
    ("Thưởng = 10%*(lương) + 5%*(doanh thu)", "Thưởng = 10%*(lương) + 5%*(doanh thu)"),
    ("Phụ cấp (*) và thưởng (*) tính theo tháng.", "Phụ cấp (*) và thưởng (*) tính theo tháng."),
    ("Chấp nhận file (*.pdf/*.docx) dưới 5MB", "Chấp nhận file (*.pdf/*.docx) dưới 5MB"),
    ("Tên file bao_cao_thang_9.xlsx", "Tên file bao_cao_thang_9.xlsx"),
    ("Họ tên: ______ Ngày sinh: ______", "Họ tên: ______ Ngày sinh: ______"),
    ("File __init__.py, link https://truongvietanh.com/hr/__draft__/a.pdf",
     "File __init__.py, link https://truongvietanh.com/hr/__draft__/a.pdf"),
    ("Lương net = Gross\n- BHXH\n+ Phụ cấp", "Lương net = Gross\n\n- BHXH\n\n+ Phụ cấp"),
    ("Hotline: +84 28 3812 3456, mã #HR04", "Hotline: +84 28 3812 3456, mã #HR04"),
    ("## Ngôn ngữ C#", "Ngôn ngữ C#"),
    ("## Mục #1 ##", "Mục #1"),
    ("Sửa file __init__.py và biến __name__.py", "Sửa file __init__.py và biến __name__.py"),
    # Ký hiệu Markdown khác bị gỡ
    # Dòng bắt đầu bằng ">" trong câu trả lời nhân sự là phép so sánh, không phải blockquote
    ("Thâm niên:\n< 5 năm: 12 ngày\n> 5 năm: 14 ngày\n>= 10 năm: 16 ngày",
     "Thâm niên:\n< 5 năm: 12 ngày\n> 5 năm: 14 ngày\n>= 10 năm: 16 ngày"),
    ("Ký tên:\n______\nNgày: ___", "Ký tên:\n______\nNgày: ___"),
    ("Mật khẩu mặc định:\n********", "Mật khẩu mặc định:\n********"),
    ("A\n--\nB", "A\n--\nB"),
    # Ý con viết bằng "+" thụt lề và dòng nối tiếp của 1 gạch đầu dòng
    ("- Mục A:\n  + ý 1\n  + ý 2\n- Mục B", "- Mục A:\n  - ý 1\n  - ý 2\n- Mục B"),
    ("- Mục A:\n  nội dung tiếp theo của mục A\n- Mục B", "- Mục A: nội dung tiếp theo của mục A\n- Mục B"),
    # ...nhưng các bước đánh số / dòng bắt đầu bằng ký hiệu dưới 1 gạch đầu dòng là ý riêng
    ("- Mục B\n  1. bước một\n  2. bước hai", "- Mục B\n\n  1. bước một\n  2. bước hai"),
    ("- a\n  > 5 năm: 14 ngày\n- b", "- a\n\n  > 5 năm: 14 ngày\n\n- b"),
    # Thẻ HTML tách dòng không được làm dính chữ
    ("Thứ 2<br/>Thứ 3<BR />Thứ 4", "Thứ 2\nThứ 3\nThứ 4"),
    ("<ul><li>Nộp đơn</li><li>Chờ duyệt</li></ul>", "- Nộp đơn\n- Chờ duyệt"),
    ("```Lương = A + B```", "Lương = A + B"),
    ("~~12 ngày~~ 14 ngày phép", "12 ngày 14 ngày phép"),
    ("_Lưu ý:_ áp dụng từ 2024", "Lưu ý: áp dụng từ 2024"),
    ("Xem [Quy định](https://truongvietanh.com/qd), liên hệ <b>HR</b><br>", "Xem Quy định (https://truongvietanh.com/qd), liên hệ HR"),
    ("Đoạn 1\n\n---\n\nĐoạn 2\n\n***", "Đoạn 1\n\nĐoạn 2"),
    ("| Bậc | Lương |\n|---|---|\n| P1 | 5.350.000đ |", "Bậc | Lương\nP1 | 5.350.000đ"),
    ("- \n- a", "- a"),
])
def test_clean_markdown_edge_cases(raw, expected):
    assert clean_markdown(raw) == expected


@pytest.mark.parametrize("raw", [
    "# # Tiêu đề",
    "    # heading thụt 4 cách",
    "**• a",
    "# ~~~",
    "Họ tên: ______ Ngày sinh: ______",
    "Các mức thưởng gồm:\n- **20/11**: 300.000đ\nÁp dụng từ 2024.",
])
def test_cleaning_is_idempotent(raw):
    """Widget làm sạch 2 lần (trong pipeline rồi trong route) — kết quả phải như nhau."""
    assert clean_markdown(clean_markdown(raw)) == clean_markdown(raw)
    assert to_plain_text(clean_markdown(raw)) == to_plain_text(raw)


def test_cleaning_stays_fast_on_degenerate_heading_line():
    """Regex heading cũ lùi theo cấp số mũ 3 với dòng '# a<1500 dấu cách>b' (5 giây/câu)."""
    import time

    started = time.perf_counter()
    clean_markdown("Đoạn mở đầu.\n## Bảng lương" + " " * 5000 + "P1\nKết thúc.")
    clean_markdown(" " * 60000 + "x")          # dòng toàn khoảng trắng dài
    clean_markdown("[a" * 15000)               # rất nhiều dấu "[" không đóng

    assert time.perf_counter() - started < 1.0


def test_plain_text_never_contains_markdown_markers():
    answer = "# Tiêu đề\n**Đậm** và *nghiêng* và `code` và __đậm 2__\n* **mục**"
    plain = to_plain_text(answer)

    assert plain == "Tiêu đề\nĐậm và nghiêng và code và đậm 2\n\n- mục"
    assert not any(marker in plain for marker in ("**", "`", "#", "__"))
    # Ký hiệu đầu dòng bị bọc đậm chỉ lộ ra sau khi bỏ ** — vẫn phải được gỡ
    assert to_plain_text("**# Tiêu đề**\n\nNội dung") == "Tiêu đề\n\nNội dung"
    assert to_plain_text("**• mục**") == "- mục"


class FakeLLM:
    def __init__(self, answer):
        self._answer = answer

    def answer(self, question, contexts, history):
        return self._answer


class FakeAdmin:
    def __init__(self):
        self.notes = []

    def create_note(self, **kwargs):
        self.notes.append(kwargs)


def make_pipeline(llm_answer, score=0.9):
    """Pipeline thật nhưng thay retrieval/LLM/Supabase bằng bản giả — không gọi mạng."""
    pipeline = AdvancedRAGPipeline.__new__(AdvancedRAGPipeline)
    pipeline.settings = type("S", (), {"min_relevance_score": 0.22, "top_k": 20})()
    pipeline.llm = FakeLLM(llm_answer)
    pipeline.admin = FakeAdmin()
    results = [{
        "id": "n:0", "text": "nội dung", "score": score,
        "metadata": {"source_file": "Quy định nghỉ phép", "heading": "Quy định > Quy trình", "version": "0.1"},
    }]
    pipeline.retrieve = lambda question, history=None: results
    return pipeline


def test_chat_returns_clean_answer_with_citations_split_out():
    pipeline = make_pipeline(
        "Bạn cần báo trước **48 giờ** qua email `hr@truongvietanh.com`.\n\n"
        "[Nguồn: Quy định nghỉ phép > Quy định > Quy trình > 0.1]"
    )

    result = pipeline.chat("Xin nghỉ phép trước bao lâu?")

    assert result["answer"] == "Bạn cần báo trước **48 giờ** qua email hr@truongvietanh.com."
    assert result["grounded"] is True
    assert result["citations"] == [
        {"source": "Quy định nghỉ phép", "heading": "Quy định > Quy trình", "version": "0.1"},
    ]


def test_chat_strips_source_tags_even_when_retrieval_is_not_grounded():
    """Retrieval dưới ngưỡng nên không có citation, nhưng model vẫn tự viết dòng nguồn:
    dòng đó không được lọt ra câu trả lời."""
    pipeline = make_pipeline("Chào bạn, mình là trợ lý.\n[Nguồn: a.md > Heading > 0.1]", score=0.05)

    result = pipeline.chat("Xin chào")

    assert result == {"answer": "Chào bạn, mình là trợ lý.", "grounded": False, "citations": []}


@pytest.mark.parametrize("llm_answer", [
    f"**{FALLBACK_ANSWER}**",
    f"{FALLBACK_ANSWER}\n[Nguồn: a.md > Heading > 0.1]",
    "[Nguồn: a.md > Heading > 0.1]",
])
def test_chat_treats_decorated_or_empty_refusal_as_fallback(llm_answer):
    pipeline = make_pipeline(llm_answer)

    result = pipeline.chat("Câu hỏi không có dữ liệu")

    assert result == {"answer": FALLBACK_ANSWER, "grounded": False, "citations": []}
    assert len(pipeline.admin.notes) == 1


def test_chat_still_answers_when_gap_note_cannot_be_saved():
    """Note [CẦN BỔ SUNG] chỉ là ghi nhận phụ: Supabase lỗi lúc ghi thì người hỏi vẫn nhận
    câu từ chối bình thường, không phải lỗi 502."""
    pipeline = make_pipeline(FALLBACK_ANSWER)

    def broken_create_note(**kwargs):
        raise RuntimeError("supabase insert failed")

    pipeline.admin.create_note = broken_create_note

    assert pipeline.chat("Câu hỏi không có dữ liệu") == {"answer": FALLBACK_ANSWER, "grounded": False, "citations": []}


def test_prompt_tells_the_model_today_so_it_never_guesses_the_year():
    from datetime import datetime, timezone

    from app.rag.prompt_builder import build_prompt, today_notice

    notice = today_notice(datetime(2026, 10, 5, 23, 30, tzinfo=timezone.utc))  # 23:30 UTC = 06:30 sáng 6/10 giờ Việt Nam
    assert "Thứ Ba, ngày 06/10/2026" in notice
    assert "HÔM NAY:" in build_prompt("tôi thử việc từ tháng 9 năm nay", [])


def test_missing_doc_marker_is_hidden_from_users_and_still_files_a_gap_note():
    from app.rag.prompt_builder import MISSING_DOC_RE

    for raw in ("[THIẾU_TÀI_LIỆU]", "**[THIẾU TÀI LIỆU]**", "[ thiếu_tài_liệu ]"):
        assert MISSING_DOC_RE.sub("", f"Trả lời.\n{raw}").strip() == "Trả lời."


# ---------- LLM qua endpoint kiểu OpenAI + dự phòng ----------
def _provider(handler, **kwargs):
    import httpx

    from app.services.llm import OpenAICompatibleLLMProvider

    provider = OpenAICompatibleLLMProvider("khoa-test", "cc/claude-sonnet-5-5", "https://endpoint.example/v1", **kwargs)
    provider._http = httpx.Client(transport=httpx.MockTransport(handler))
    return provider


def test_endpoint_provider_sends_bearer_model_and_no_openrouter_headers():
    import json

    seen = {}

    def handler(request):
        seen.update(url=str(request.url), auth=request.headers["authorization"], referer=request.headers.get("http-referer"),
                    body=json.loads(request.content))
        return httpx_response({"choices": [{"message": {"content": "  Xin chào  "}}]})

    answer = _provider(handler).answer("hỏi?", [], None)
    assert answer == "Xin chào"
    assert seen["url"] == "https://endpoint.example/v1/chat/completions" and seen["auth"] == "Bearer khoa-test"
    assert seen["referer"] is None and seen["body"]["model"] == "cc/claude-sonnet-5-5" and seen["body"]["max_tokens"] == 2000


def httpx_response(payload=None, status=200, text=None):
    import httpx

    return httpx.Response(status, json=payload) if text is None else httpx.Response(status, text=text)


def test_endpoint_provider_rejects_html_and_error_pages_instead_of_showing_them_to_staff():
    import pytest

    for response in (httpx_response(text="<html>tunnel offline</html>"), httpx_response({"error": "x"}, status=502),
                     httpx_response({"choices": []}), httpx_response({"content": [{"text": "kiểu anthropic"}]})):
        with pytest.raises(RuntimeError):
            _provider(lambda request, r=response: r).answer("q", [], None)
    # việc phụ (viết lại câu hỏi) lỗi thì trả rỗng, không ném
    assert _provider(lambda request: httpx_response(text="<html>")).complete("p") == ""


def test_endpoint_provider_limits_concurrent_calls():
    import threading
    import time

    active, peak, lock = 0, 0, threading.Lock()

    def handler(request):
        nonlocal active, peak
        with lock:
            active += 1
            peak = max(peak, active)
        time.sleep(0.05)
        with lock:
            active -= 1
        return httpx_response({"choices": [{"message": {"content": "ok"}}]})

    provider = _provider(handler, max_concurrency=2)
    threads = [threading.Thread(target=provider.answer, args=("q", [], None)) for _ in range(8)]
    [t.start() for t in threads]
    [t.join() for t in threads]
    assert peak <= 2


def test_fallback_switches_to_backup_and_skips_a_dead_primary_for_a_while():
    from app.services.llm import FallbackLLMProvider, LLMProvider

    class Stub(LLMProvider):
        def __init__(self, reply=None):
            self.reply, self.calls = reply, 0

        def answer(self, question, contexts, history=None):
            self.calls += 1
            if self.reply is None:
                raise RuntimeError("tunnel tắt")
            return self.reply

        def describe_image(self, image_bytes, mime_type):
            return ""

    primary, backup = Stub(), Stub("từ dự phòng")
    provider = FallbackLLMProvider(primary, backup, cooldown=60)
    assert provider.answer("q", []) == "từ dự phòng" and provider.answer("q", []) == "từ dự phòng"
    assert primary.calls == 1 and backup.calls == 2  # lần 2 không thử lại phương án chính đang chết
    provider._skip_primary_until = 0  # hết thời gian nghỉ
    primary.reply = "chính hồi phục"
    assert provider.answer("q", []) == "chính hồi phục"


def test_pipeline_builds_the_right_llm_for_each_setting(monkeypatch):
    from types import SimpleNamespace

    from app.rag.pipeline import AdvancedRAGPipeline
    from app.services.llm import FallbackLLMProvider, OpenAICompatibleLLMProvider

    base = dict(gemini_api_key=None, gemini_model="g", openrouter_api_key="or-key", openrouter_model="m", openrouter_base_url="https://openrouter.ai/api/v1",
                llm_base_url="https://endpoint.example/v1", llm_api_key="k", llm_model="cc/claude-sonnet-5-5")
    chosen = AdvancedRAGPipeline._build_llm(SimpleNamespace(llm_provider="openai_compatible", **base))
    assert isinstance(chosen, FallbackLLMProvider) and chosen.primary.model == "cc/claude-sonnet-5-5" and chosen.secondary.model == "m"
    solo = AdvancedRAGPipeline._build_llm(SimpleNamespace(llm_provider="openai_compatible", **{**base, "openrouter_api_key": None}))
    assert isinstance(solo, OpenAICompatibleLLMProvider)
    # Thiếu key/URL: không chết lúc khởi động, quay về OpenRouter
    fallback = AdvancedRAGPipeline._build_llm(SimpleNamespace(llm_provider="openai_compatible", **{**base, "llm_api_key": None}))
    assert isinstance(fallback, OpenAICompatibleLLMProvider) and fallback.model == "m"


def test_endpoint_provider_asks_for_a_single_json_and_can_still_join_an_sse_stream():
    import json

    sent = {}

    def handler(request):
        sent.update(json.loads(request.content))
        chunks = ['{"choices":[{"delta":{"role":"assistant"}}]}', '{"choices":[{"delta":{"content":"Xin "}}]}',
                  '{"choices":[{"delta":{"content":"chào"}}]}', '{"choices":[{"delta":{},"finish_reason":"stop"}]}']
        return httpx_response(text="".join(f"data: {c}\n\n" for c in chunks) + "data: [DONE]\n\n")

    assert _provider(handler).answer("q", [], None) == "Xin chào"
    assert sent["stream"] is False
