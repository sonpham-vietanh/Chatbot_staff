import json
import logging
import threading
import time
from abc import ABC, abstractmethod

import httpx

from app.rag.prompt_builder import FALLBACK_ANSWER, build_prompt


logger = logging.getLogger(__name__)

MAX_RETRY_AFTER = 15.0
"""Chờ tối đa bấy nhiêu giây theo header Retry-After khi bị 429 — lâu hơn thì người dùng bỏ đi, không đáng chờ."""


class LLMError(RuntimeError):
    """Lỗi gọi dịch vụ AI, kèm mã HTTP để lớp API đưa ra thông báo đúng nguyên nhân (không lộ địa chỉ/khoá endpoint)."""

    def __init__(self, message: str, status: int | None = None, retry_after: float | None = None):
        super().__init__(message)
        self.status = status
        self.retry_after = retry_after

    @property
    def user_message(self) -> str:
        if self.status in (401, 403):
            return "Khoá truy cập dịch vụ AI không đúng hoặc đã bị khoá. Hãy báo quản trị viên để kiểm tra khoá."
        if self.status == 402:
            return "Dịch vụ AI đã hết hạn mức. Hãy báo quản trị viên."
        if self.status == 429:
            return "Dịch vụ AI đang quá tải, hãy thử lại sau ít giây."
        return "Dịch vụ AI tạm thời không phản hồi (có thể máy chạy dịch vụ đang tắt). Hãy thử lại sau ít phút."

IMAGE_DESCRIBE_PROMPT = (
    "Mô tả ngắn gọn nội dung hình ảnh này bằng tiếng Việt. Nếu ảnh chứa bảng số liệu, chữ, "
    "biểu đồ hay văn bản — hãy chép lại chính xác toàn bộ chữ/số đó. Nếu ảnh chỉ mang tính "
    "minh hoạ/trang trí không có thông tin, trả lời đúng 1 câu: 'Ảnh minh hoạ, không có nội dung văn bản.'"
)


class LLMProvider(ABC):
    @abstractmethod
    def answer(self, question: str, contexts: list[dict], history: list[dict] | None = None) -> str:
        ...

    @abstractmethod
    def describe_image(self, image_bytes: bytes, mime_type: str) -> str:
        ...

    def complete(self, prompt: str, max_tokens: int = 200) -> str:
        """Gọi ngắn, không guardrail — dùng cho việc phụ như viết lại câu hỏi nối tiếp. Rỗng = không hỗ trợ."""
        return ""


class MockLLMProvider(LLMProvider):
    """Provider gia lap (khong goi LLM that) - dung khi EMBEDDING_PROVIDER/LLM_PROVIDER
    chua cau hinh key that, hoac cho test khong phu thuoc mang."""

    def answer(self, question: str, contexts: list[dict], history: list[dict] | None = None) -> str:
        if not contexts:
            return FALLBACK_ANSWER
        first = contexts[0]
        metadata = first["metadata"]
        source = (
            f"[Nguồn: {metadata.get('source')} > {metadata.get('heading', 'Nội dung chung')} > "
            f"{metadata.get('version', 'unknown')}]"
        )
        excerpt = first["text"].replace("\n", " ").strip()
        return f"Theo tài liệu đã được phê duyệt: {excerpt}\n\n{source}"

    def describe_image(self, image_bytes: bytes, mime_type: str) -> str:
        return ""


class GeminiLLMProvider(LLMProvider):
    """Gemini grounded generation; không gửi câu hỏi nếu pipeline không có context."""

    def __init__(self, api_key: str, model: str):
        from google import genai
        from google.genai import types

        self.client = genai.Client(api_key=api_key)
        self.model = model
        self.config = types.GenerateContentConfig(
            temperature=0.3,
            max_output_tokens=2000,
        )

    def answer(self, question: str, contexts: list[dict], history: list[dict] | None = None) -> str:
        response = self.client.models.generate_content(
            model=self.model,
            contents=build_prompt(question, contexts, history),
            config=self.config,
        )
        answer = (response.text or "").strip()
        return answer or FALLBACK_ANSWER

    def complete(self, prompt: str, max_tokens: int = 200) -> str:
        from google.genai import types

        response = self.client.models.generate_content(
            model=self.model, contents=prompt,
            config=types.GenerateContentConfig(temperature=0.0, max_output_tokens=max_tokens))
        return (response.text or "").strip()

    def describe_image(self, image_bytes: bytes, mime_type: str) -> str:
        from google.genai import types

        response = self.client.models.generate_content(
            model=self.model,
            contents=[types.Part.from_bytes(data=image_bytes, mime_type=mime_type), IMAGE_DESCRIBE_PROMPT],
        )
        return (response.text or "").strip()


class OpenAICompatibleLLMProvider(LLMProvider):
    """Gọi model qua API kiểu OpenAI (`POST {base}/chat/completions`, `Authorization: Bearer`): dùng cho cả
    OpenRouter lẫn endpoint tự dựng (vd. proxy Claude của công ty). Dùng httpx.Client tái sử dụng kết nối (instance
    này là singleton dùng chung cả app).

    `max_concurrency` chặn số lời gọi đồng thời MỖI worker — endpoint tự dựng chạy trên máy cá nhân chỉ chịu được
    khoảng 6–9 lời gọi cùng lúc, mà app chạy nhiều worker."""

    def __init__(self, api_key: str, model: str, base_url: str, max_concurrency: int = 2, label: str = "LLM"):
        self.api_key = api_key
        self.model = model
        self.label = label
        self.url = f"{base_url.rstrip('/')}/chat/completions"
        self._http = httpx.Client(timeout=httpx.Timeout(90, connect=10))
        self._slots = threading.BoundedSemaphore(max(1, max_concurrency))
        self._is_openrouter = "openrouter.ai" in base_url

    def _headers(self) -> dict[str, str]:
        headers = {"Authorization": f"Bearer {self.api_key}", "Content-Type": "application/json"}
        if self._is_openrouter:  # chỉ OpenRouter dùng 2 header định danh này; endpoint khác không cần biết
            headers.update({"HTTP-Referer": "http://127.0.0.1:5173", "X-Title": "Viet Anh Staff Assistant"})
        return headers

    def _chat(self, messages: list[dict], max_tokens: int, temperature: float, timeout: float) -> str:
        """Một lời gọi chat. Lỗi HTTP, trả về không phải JSON (vd. trang HTML khi máy chạy endpoint tắt), hay
        không có nội dung đều ném RuntimeError để lớp trên quyết định (báo lỗi hoặc chuyển phương án dự phòng)."""
        payload = {"model": self.model, "messages": messages, "temperature": temperature, "max_tokens": max_tokens, "stream": False}
        for attempt in (1, 2):
            try:
                with self._slots:
                    response = self._http.post(self.url, headers=self._headers(), timeout=timeout, json=payload)
            except httpx.HTTPError as error:  # không kết nối được / hết thời gian: máy chạy endpoint hoặc đường hầm tắt
                raise LLMError(f"{self.label} không kết nối được: {type(error).__name__}") from error
            if response.status_code == 429 and attempt == 1:
                # Gọi quá nhanh: chờ đúng số giây endpoint yêu cầu (nhả slot trong lúc chờ) rồi thử lại một lần
                time.sleep(self._retry_after(response))
                continue
            break
        if response.is_error:
            raise LLMError(f"{self.label} HTTP {response.status_code}: {response.text[:300]}",
                           response.status_code, self._retry_after(response) if response.status_code == 429 else None)
        try:
            if response.text.lstrip().startswith("data:"):  # endpoint vẫn trả luồng SSE dù đã xin stream=false
                content = self._join_stream(response.text)
            else:
                content = response.json()["choices"][0]["message"]["content"]
        except (ValueError, KeyError, IndexError, TypeError) as error:
            raise LLMError(f"{self.label} trả về dữ liệu không đúng dạng tin nhắn: {response.text[:200]!r}", 502) from error
        if isinstance(content, list):
            content = "".join(part.get("text", "") for part in content if isinstance(part, dict))
        return str(content or "").strip()

    @staticmethod
    def _retry_after(response: httpx.Response) -> float:
        try:
            seconds = float(response.headers.get("retry-after", ""))
        except ValueError:
            seconds = 2.0
        return min(max(seconds, 0.0), MAX_RETRY_AFTER)

    @staticmethod
    def _join_stream(raw: str) -> str:
        """Ghép các mẩu `data: {...chat.completion.chunk...}` của một phản hồi SSE thành nội dung hoàn chỉnh."""
        parts: list[str] = []
        for line in raw.splitlines():
            line = line.strip()
            if not line.startswith("data:") or line.endswith("[DONE]"):
                continue
            for choice in json.loads(line[5:]).get("choices", []):
                piece = (choice.get("delta") or {}).get("content")
                if piece:
                    parts.append(piece)
        if not parts:
            raise ValueError("luồng không có nội dung")
        return "".join(parts)

    def answer(self, question: str, contexts: list[dict], history: list[dict] | None = None) -> str:
        text = self._chat([{"role": "user", "content": build_prompt(question, contexts, history)}], 2000, 0.3, 90)
        return text or FALLBACK_ANSWER

    def complete(self, prompt: str, max_tokens: int = 200) -> str:
        try:
            return self._chat([{"role": "user", "content": prompt}], max_tokens, 0.0, 25)
        except Exception:
            return ""  # việc phụ (viết lại câu hỏi): lỗi thì lớp trên dùng cách nối chuỗi đơn giản

    def describe_image(self, image_bytes: bytes, mime_type: str) -> str:
        import base64

        data_uri = f"data:{mime_type};base64,{base64.b64encode(image_bytes).decode('ascii')}"
        try:
            return self._chat([{"role": "user", "content": [
                {"type": "text", "text": IMAGE_DESCRIBE_PROMPT},
                {"type": "image_url", "image_url": {"url": data_uri}},
            ]}], 500, 0.1, 60)
        except Exception:
            return ""


OpenRouterLLMProvider = OpenAICompatibleLLMProvider  # tên cũ, giữ để mã/test khác không phải đổi


class FallbackLLMProvider(LLMProvider):
    """Gọi phương án chính; lỗi thì chuyển sang phương án dự phòng. Sau một lần chính lỗi, bỏ qua nó `cooldown`
    giây để lúc máy chạy endpoint tắt, mỗi câu hỏi không phải chờ hết thời gian chờ của phương án chính."""

    def __init__(self, primary: LLMProvider, secondary: LLMProvider, cooldown: float = 60.0):
        self.primary = primary
        self.secondary = secondary
        self.cooldown = cooldown
        self._skip_primary_until = 0.0
        self._primary_error: Exception | None = None

    def answer(self, question: str, contexts: list[dict], history: list[dict] | None = None) -> str:
        if time.monotonic() >= self._skip_primary_until:
            try:
                return self.primary.answer(question, contexts, history)
            except Exception as error:
                logger.warning("LLM chính lỗi, chuyển sang phương án dự phòng trong %.0fs", self.cooldown, exc_info=True)
                self._skip_primary_until = time.monotonic() + self.cooldown
                self._primary_error = error
        try:
            return self.secondary.answer(question, contexts, history)
        except Exception:
            # Cả hai đều lỗi: báo lỗi của phương án CHÍNH (nguyên nhân gốc), không phải lỗi của phương án dự phòng
            if self._primary_error is not None:
                raise self._primary_error
            raise

    def complete(self, prompt: str, max_tokens: int = 200) -> str:
        provider = self.secondary if time.monotonic() < self._skip_primary_until else self.primary
        return provider.complete(prompt, max_tokens)

    def describe_image(self, image_bytes: bytes, mime_type: str) -> str:
        provider = self.secondary if time.monotonic() < self._skip_primary_until else self.primary
        return provider.describe_image(image_bytes, mime_type)


def build_llm_provider(name: str, api_key: str | None = None,
                       model: str = "gemini-2.5-flash", base_url: str = "https://openrouter.ai/api/v1",
                       max_concurrency: int | None = None) -> LLMProvider:
    if name == "mock":
        return MockLLMProvider()
    if name == "gemini":
        if not api_key:
            raise ValueError("GEMINI_API_KEY chưa được cấu hình trong file .env")
        return GeminiLLMProvider(api_key, model)
    if name == "openrouter":
        if not api_key:
            raise ValueError("OPENROUTER_API_KEY chưa được cấu hình trong file .env")
        return OpenAICompatibleLLMProvider(api_key, model, base_url, max_concurrency=8, label="OpenRouter")
    if name == "openai_compatible":
        if not api_key or not base_url:
            raise ValueError("LLM_API_KEY và LLM_BASE_URL phải được cấu hình khi LLM_PROVIDER=openai_compatible")
        if not model.startswith("cc/"):
            logger.warning("LLM_MODEL=%r không có tiền tố 'cc/' — endpoint sẽ báo không tìm thấy model", model)
        return OpenAICompatibleLLMProvider(api_key, model, base_url, max_concurrency=max_concurrency or 2, label="LLM endpoint")
    raise ValueError(f"LLM provider chưa được hỗ trợ: {name}")
