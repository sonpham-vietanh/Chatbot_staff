"""Chat dạng stream (/api/chat-staff/stream): chuỗi sự kiện, `done` khớp /chat-staff, rơi về dự phòng, lỗi giữa chừng."""
import json
from types import SimpleNamespace

import httpx
from fastapi.testclient import TestClient

from app.api import routes
from app.main import app
from app.rag.pipeline import AdvancedRAGPipeline
from app.services.llm import FallbackLLMProvider, LLMError, LLMProvider, OpenAICompatibleLLMProvider

RAW = "Bạn cần báo trước **48 giờ** qua email `hr@truongvietanh.com`.\n\n[Nguồn: Quy định nghỉ phép > Quy định > Quy trình > 0.1]"
CHUNKS = ["Bạn cần báo trước **48 giờ** ", "qua email `hr@truongvietanh.com`.\n\n", "[Nguồn: Quy định nghỉ phép > Quy định > Quy trình > 0.1]"]


class ScriptedLLM(LLMProvider):
    """Stream đúng các mẩu cho trước; `fail_after` = ném lỗi sau ngần ấy mẩu (None = không lỗi)."""

    def __init__(self, chunks, fail_after=None):
        self.chunks, self.fail_after, self.stream_calls = chunks, fail_after, 0

    def answer(self, question, contexts, history=None, asker=None):
        return "".join(self.chunks).strip()

    def describe_image(self, image_bytes, mime_type):
        return ""

    def answer_stream(self, question, contexts, history=None, asker=None):
        self.stream_calls += 1
        for index, chunk in enumerate(self.chunks):
            if self.fail_after is not None and index >= self.fail_after:
                raise LLMError("endpoint tắt", 503)
            yield chunk


def make_rag(llm):
    pipeline = AdvancedRAGPipeline.__new__(AdvancedRAGPipeline)
    pipeline.settings = SimpleNamespace(min_relevance_score=0.22, top_k=20)
    pipeline.llm = llm
    pipeline._spawn = lambda target, *args: None
    pipeline.supabase = SimpleNamespace(insert=lambda *a, **k: None)
    pipeline.usage = None
    pipeline.wigs = pipeline.profiles = pipeline.points = None
    results = [{"id": "n:0", "text": "nội dung", "score": 0.9,
                "metadata": {"source_file": "Quy định nghỉ phép", "heading": "Quy định > Quy trình", "version": "0.1"}}]
    pipeline.retrieve = lambda question, history=None: results
    return pipeline


def call(rag, path, question="Xin nghỉ phép trước bao lâu?"):
    app.dependency_overrides[routes.require_user] = lambda: {"id": "u1", "email": "lan@truongvietanh.com", "user_metadata": {}}
    app.dependency_overrides[routes.get_rag_service] = lambda: rag
    app.dependency_overrides[routes.get_chat_history_service] = lambda: SimpleNamespace(
        thread_belongs_to=lambda *a: True, create_thread_with_id=lambda *a: None, add_turn=lambda *a: None, list_messages=lambda *a: [])
    try:
        return TestClient(app).post(path, json={"question": question, "thread_id": "5b3f1f0e-0000-4000-8000-000000000001"})
    finally:
        app.dependency_overrides.clear()


def events(response):
    parsed = []
    for block in response.text.strip().split("\n\n"):
        name, data = block.split("\n", 1)
        parsed.append((name.removeprefix("event: "), json.loads(data.removeprefix("data: "))))
    return parsed


def test_stream_sends_deltas_then_a_done_identical_to_the_plain_endpoint():
    rag = make_rag(ScriptedLLM(CHUNKS))
    streamed = call(rag, "/api/chat-staff/stream")
    assert streamed.status_code == 200 and streamed.headers["content-type"].startswith("text/event-stream")
    assert streamed.headers["cache-control"].startswith("no-cache") and streamed.headers["x-accel-buffering"] == "no"
    parsed = events(streamed)
    assert [name for name, _ in parsed] == ["delta", "delta", "delta", "done"]
    assert "".join(data["text"] for name, data in parsed if name == "delta") == RAW
    plain = call(rag, "/api/chat-staff").json()
    done = parsed[-1][1]
    assert done == plain                              # cùng answer đã làm sạch, citations, suggestions, thread_id, grounded
    assert done["answer"] == "Bạn cần báo trước **48 giờ** qua email hr@truongvietanh.com." and done["grounded"] is True
    assert done["thread_id"] == "5b3f1f0e-0000-4000-8000-000000000001" and done["citations"][0]["source"] == "Quy định nghỉ phép"


def test_quick_reply_is_a_single_done_event_without_calling_the_llm():
    llm = ScriptedLLM(CHUNKS)
    rag = make_rag(llm)
    rag._quick_reply = AdvancedRAGPipeline._quick_reply
    parsed = events(call(rag, "/api/chat-staff/stream", "xin chào"))
    assert [name for name, _ in parsed] == ["done"] and "trợ lý nội bộ" in parsed[0][1]["answer"]
    assert llm.stream_calls == 0


def test_provider_without_streaming_falls_back_to_one_chunk():
    class Plain(LLMProvider):
        def answer(self, *args, **kwargs):
            return RAW

        def describe_image(self, *args):
            return ""

    parsed = events(call(make_rag(Plain()), "/api/chat-staff/stream"))
    assert [name for name, _ in parsed] == ["delta", "done"] and parsed[0][1]["text"] == RAW


def test_primary_failing_before_any_token_switches_to_the_secondary():
    primary, secondary = ScriptedLLM(CHUNKS, fail_after=0), ScriptedLLM(CHUNKS)
    rag = make_rag(FallbackLLMProvider(primary, secondary))
    parsed = events(call(rag, "/api/chat-staff/stream"))
    assert parsed[-1][0] == "done" and parsed[-1][1]["grounded"] is True and secondary.stream_calls == 1


def test_failure_after_tokens_sends_a_vietnamese_error_event_and_saves_nothing():
    saved = []
    rag = make_rag(FallbackLLMProvider(ScriptedLLM(CHUNKS, fail_after=1), ScriptedLLM(CHUNKS)))
    app.dependency_overrides[routes.require_user] = lambda: {"id": "u1", "email": "lan@truongvietanh.com", "user_metadata": {}}
    app.dependency_overrides[routes.get_rag_service] = lambda: rag
    app.dependency_overrides[routes.get_chat_history_service] = lambda: SimpleNamespace(
        thread_belongs_to=lambda *a: True, create_thread_with_id=lambda *a: saved.append(1), add_turn=lambda *a: saved.append(2), list_messages=lambda *a: [])
    try:
        response = TestClient(app).post("/api/chat-staff/stream", json={"question": "Xin nghỉ phép trước bao lâu?"})
    finally:
        app.dependency_overrides.clear()
    parsed = events(response)
    assert [name for name, _ in parsed] == ["delta", "error"] and "gián đoạn" in parsed[-1][1]["detail"] and saved == []


def test_total_failure_before_any_token_is_a_plain_http_error():
    rag = make_rag(FallbackLLMProvider(ScriptedLLM(CHUNKS, fail_after=0), ScriptedLLM(CHUNKS, fail_after=0)))
    response = call(rag, "/api/chat-staff/stream")
    assert response.status_code == 503 and "AI" in response.json()["detail"]


def _openai(handler):
    provider = OpenAICompatibleLLMProvider("k", "m", "http://llm.test/v1")
    provider._http = httpx.Client(transport=httpx.MockTransport(handler))
    return provider


def test_openai_provider_streams_sse_chunks():
    body = "".join(f"data: {json.dumps({'choices': [{'delta': {'content': piece}}]})}\n\n" for piece in ("Xin ", "chào")) + "data: [DONE]\n\n"
    seen = []

    def handler(request):
        seen.append(json.loads(request.content))
        return httpx.Response(200, text=body, headers={"content-type": "text/event-stream"})

    assert list(_openai(handler).answer_stream("q", [], [])) == ["Xin ", "chào"] and seen[0]["stream"] is True


def test_openai_provider_raises_llm_error_on_http_error_or_empty_stream():
    for response in (httpx.Response(402, text="het tien"), httpx.Response(200, text="data: [DONE]\n\n")):
        try:
            list(_openai(lambda request, r=response: r).answer_stream("q", [], []))
        except LLMError:
            continue
        raise AssertionError("phải ném LLMError")
