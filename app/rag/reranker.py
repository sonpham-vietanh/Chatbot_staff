from typing import Any, Protocol


class Reranker(Protocol):
    def rerank(self, query: str, results: list[dict[str, Any]]) -> list[dict[str, Any]]:
        ...


class NoOpReranker:
    """Điểm mở rộng để cắm cross-encoder/reranker sau này."""

    def rerank(self, query: str, results: list[dict[str, Any]]) -> list[dict[str, Any]]:
        return results
