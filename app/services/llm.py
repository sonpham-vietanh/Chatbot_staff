from abc import ABC, abstractmethod

import httpx

from app.rag.prompt_builder import FALLBACK_ANSWER, build_prompt


class LLMProvider(ABC):
    @abstractmethod
    def answer(self, question: str, contexts: list[dict], history: list[dict] | None = None) -> str:
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


class OpenRouterLLMProvider(LLMProvider):
    """Gọi model OpenRouter qua API tương thích OpenAI."""

    def __init__(self, api_key: str, model: str, base_url: str):
        self.api_key = api_key
        self.model = model
        self.url = f"{base_url.rstrip('/')}/chat/completions"

    def answer(self, question: str, contexts: list[dict], history: list[dict] | None = None) -> str:
        response = httpx.post(
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
