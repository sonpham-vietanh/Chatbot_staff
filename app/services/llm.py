from abc import ABC, abstractmethod

import httpx

from app.rag.prompt_builder import FALLBACK_ANSWER, build_prompt


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


class MockLLMProvider(LLMProvider):
    """TODO: thay bằng Gemini/OpenAI provider khi có key và chính sách triển khai."""

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
            temperature=0.15,
            max_output_tokens=700,
        )

    def answer(self, question: str, contexts: list[dict], history: list[dict] | None = None) -> str:
        response = self.client.models.generate_content(
            model=self.model,
            contents=build_prompt(question, contexts, history),
            config=self.config,
        )
        answer = (response.text or "").strip()
        return answer or FALLBACK_ANSWER

    def describe_image(self, image_bytes: bytes, mime_type: str) -> str:
        from google.genai import types

        response = self.client.models.generate_content(
            model=self.model,
            contents=[types.Part.from_bytes(data=image_bytes, mime_type=mime_type), IMAGE_DESCRIBE_PROMPT],
        )
        return (response.text or "").strip()


class OpenRouterLLMProvider(LLMProvider):
    """Gọi model OpenRouter qua API tương thích OpenAI. Dùng httpx.Client tái sử dụng kết
    nối (instance này là singleton dùng chung cả app) thay vì mở TCP/TLS mới mỗi lần chat."""

    def __init__(self, api_key: str, model: str, base_url: str):
        self.api_key = api_key
        self.model = model
        self.url = f"{base_url.rstrip('/')}/chat/completions"
        self._http = httpx.Client(timeout=90)

    def answer(self, question: str, contexts: list[dict], history: list[dict] | None = None) -> str:
        response = self._http.post(
            self.url,
            headers={
                "Authorization": f"Bearer {self.api_key}",
                "Content-Type": "application/json",
                "HTTP-Referer": "http://127.0.0.1:5173",
                "X-Title": "Viet Anh Staff Assistant",
            },
            json={
                "model": self.model,
                "messages": [{"role": "user", "content": build_prompt(question, contexts, history)}],
                "temperature": 0.15,
                "max_tokens": 700,
            },
            timeout=90,
        )
        if response.is_error:
            raise RuntimeError(f"OpenRouter HTTP {response.status_code}: {response.text[:500]}")
        payload = response.json()
        answer = payload.get("choices", [{}])[0].get("message", {}).get("content", "")
        if isinstance(answer, list):
            answer = "".join(part.get("text", "") for part in answer if isinstance(part, dict))
        return str(answer).strip() or FALLBACK_ANSWER

    def describe_image(self, image_bytes: bytes, mime_type: str) -> str:
        import base64

        data_uri = f"data:{mime_type};base64,{base64.b64encode(image_bytes).decode('ascii')}"
        response = self._http.post(
            self.url,
            headers={
                "Authorization": f"Bearer {self.api_key}",
                "Content-Type": "application/json",
                "HTTP-Referer": "http://127.0.0.1:5173",
                "X-Title": "Viet Anh Staff Assistant",
            },
            json={
                "model": self.model,
                "messages": [{
                    "role": "user",
                    "content": [
                        {"type": "text", "text": IMAGE_DESCRIBE_PROMPT},
                        {"type": "image_url", "image_url": {"url": data_uri}},
                    ],
                }],
                "temperature": 0.1,
                "max_tokens": 500,
            },
            timeout=60,
        )
        if response.is_error:
            return ""
        payload = response.json()
        answer = payload.get("choices", [{}])[0].get("message", {}).get("content", "")
        if isinstance(answer, list):
            answer = "".join(part.get("text", "") for part in answer if isinstance(part, dict))
        return str(answer).strip()


def build_llm_provider(name: str, api_key: str | None = None,
                       model: str = "gemini-2.5-flash", base_url: str = "https://openrouter.ai/api/v1") -> LLMProvider:
    if name == "mock":
        return MockLLMProvider()
    if name == "gemini":
        if not api_key:
            raise ValueError("GEMINI_API_KEY chưa được cấu hình trong file .env")
        return GeminiLLMProvider(api_key, model)
    if name == "openrouter":
        if not api_key:
            raise ValueError("OPENROUTER_API_KEY chưa được cấu hình trong file .env")
        return OpenRouterLLMProvider(api_key, model, base_url)
    raise ValueError(f"LLM provider chưa được hỗ trợ: {name}")
