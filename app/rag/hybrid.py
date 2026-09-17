from typing import Any, Protocol


class LexicalRetriever(Protocol):
    """Interface để thêm BM25/keyword retrieval và trộn với vector score."""

    def search(self, query: str, top_k: int, filters: dict[str, str | None]) -> list[dict[str, Any]]:
        ...


class NoOpLexicalRetriever:
    """TODO: cắm BM25 khi corpus lớn; MVP dùng vector retrieval làm nguồn chính."""

    def search(self, query: str, top_k: int, filters: dict[str, str | None]) -> list[dict[str, Any]]:
        return []


def reciprocal_rank_fusion(*result_sets: list[dict[str, Any]], top_k: int) -> list[dict[str, Any]]:
    """Gộp nhiều danh sách retrieval theo RRF, sẵn sàng dùng cho hybrid search."""
    scores: dict[str, float] = {}
    items: dict[str, dict[str, Any]] = {}
    for result_set in result_sets:
        for rank, item in enumerate(result_set, start=1):
            item_id = item["id"]
            scores[item_id] = scores.get(item_id, 0.0) + 1.0 / (60 + rank)
            items[item_id] = item
    for item_id, item in items.items():
        item["score"] = scores[item_id]
    return sorted(items.values(), key=lambda item: item["score"], reverse=True)[:top_k]
