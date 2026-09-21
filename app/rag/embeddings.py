from abc import ABC, abstractmethod
import hashlib
import math
import re

import httpx


class EmbeddingProvider(ABC):
    @abstractmethod
    def embed(self, text: str) -> list[float]:
        """Chuyển văn bản thành vector."""

    def embed_batch(self, texts: list[str]) -> list[list[float]]:
        """Mặc định gọi embed() tuần tự; provider hỗ trợ batch thật nên override để nhanh hơn
        nhiều khi reindex hàng trăm chunk (giảm thời gian collection bị rỗng giữa chừng)."""
        return [self.embed(text) for text in texts]


class MockEmbeddingProvider(EmbeddingProvider):
    """Embedding deterministic để chạy demo local, không cần API key."""

    def __init__(self, dimensions: int = 384):
        self.dimensions = dimensions

    def embed(self, text: str) -> list[float]:
        vector = [0.0] * self.dimensions
        tokens = re.findall(r"\w+", text.lower(), flags=re.UNICODE)
        for token in tokens:
            digest = hashlib.sha256(token.encode("utf-8")).digest()
            index = int.from_bytes(digest[:4], "big") % self.dimensions
            vector[index] += 1.0 if digest[4] % 2 else -1.0
        norm = math.sqrt(sum(value * value for value in vector)) or 1.0
        return [value / norm for value in vector]


class GeminiEmbeddingProvider(EmbeddingProvider):
    """Embedding thật qua Gemini; API key chỉ lấy từ biến môi trường."""

    def __init__(self, api_key: str, model: str):
        from google import genai

        self.client = genai.Client(api_key=api_key)
        self.model = model

    def embed(self, text: str) -> list[float]:
        response = self.client.models.embed_content(model=self.model, contents=text)
        return list(response.embeddings[0].values)


class OpenRouterEmbeddingProvider(EmbeddingProvider):
    """Embedding qua OpenRouter, dùng chung API key với LLM (API tương thích OpenAI).
    Dùng httpx.Client tái sử dụng kết nối (instance này là singleton dùng chung cả app)
    thay vì mở TCP/TLS mới mỗi lần embed — mỗi câu hỏi chat đều gọi qua đây."""

    def __init__(self, api_key: str, model: str, base_url: str):
        self.api_key = api_key
        self.model = model
        self.url = f"{base_url.rstrip('/')}/embeddings"
        self._http = httpx.Client(timeout=120)

    BATCH_SIZE = 64

    def embed(self, text: str) -> list[float]:
        return self.embed_batch([text])[0]

    def embed_batch(self, texts: list[str]) -> list[list[float]]:
        if not texts:
            return []
        results: list[list[float]] = []
        for start in range(0, len(texts), self.BATCH_SIZE):
            batch = texts[start:start + self.BATCH_SIZE]
            response = self._http.post(
                self.url,
                headers={
                    "Authorization": f"Bearer {self.api_key}",
                    "Content-Type": "application/json",
                    "HTTP-Referer": "http://127.0.0.1:5173",
                    "X-Title": "Viet Anh Staff Assistant",
                },
                json={"model": self.model, "input": batch},
                timeout=120,
            )
            if response.is_error:
                raise RuntimeError(f"OpenRouter HTTP {response.status_code}: {response.text[:500]}")
            payload = response.json()
            ordered = sorted(payload["data"], key=lambda item: item["index"])
            results.extend(item["embedding"] for item in ordered)
        return results


def build_embedding_provider(name: str, api_key: str | None = None,
                             model: str = "gemini-embedding-001",
                             base_url: str = "https://openrouter.ai/api/v1") -> EmbeddingProvider:
    if name == "mock":
        return MockEmbeddingProvider()
    if name == "gemini":
        if not api_key:
            raise ValueError("GEMINI_API_KEY chưa được cấu hình trong file .env")
        return GeminiEmbeddingProvider(api_key, model)
    if name == "openrouter":
        if not api_key:
            raise ValueError("OPENROUTER_API_KEY chưa được cấu hình trong file .env")
        return OpenRouterEmbeddingProvider(api_key, model, base_url)
    raise ValueError(f"Embedding provider chưa được hỗ trợ: {name}")
