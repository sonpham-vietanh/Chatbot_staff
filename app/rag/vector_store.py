from typing import Any

from .embeddings import EmbeddingProvider


class VectorStore:
    """Adapter Chroma; import lazy để unit test không cần khởi tạo database."""

    def __init__(self, path: str, collection_name: str, embedding_provider: EmbeddingProvider):
        import chromadb

        self.embedding_provider = embedding_provider
        client = chromadb.PersistentClient(path=path)
        self.client = client
        self.collection_name = collection_name
        self.collection = client.get_or_create_collection(
            name=collection_name,
            metadata={"hnsw:space": "cosine"},
        )

    def reset(self) -> None:
        """Tạo collection mới để tránh giữ dimension của provider cũ."""
        try:
            self.client.delete_collection(name=self.collection_name)
        except Exception:
            pass
        self.collection = self.client.get_or_create_collection(
            name=self.collection_name,
            metadata={"hnsw:space": "cosine"},
        )

    def upsert(self, chunks: list[dict[str, Any]]) -> None:
        if not chunks:
            return
        self.collection.upsert(
            ids=[chunk["id"] for chunk in chunks],
            documents=[chunk["text"] for chunk in chunks],
            embeddings=[self.embedding_provider.embed(chunk["text"]) for chunk in chunks],
            metadatas=[chunk["metadata"] for chunk in chunks],
        )

    def search(self, query: str, top_k: int, filters: dict[str, str | None] | None = None) -> list[dict[str, Any]]:
        where = self._build_where(filters or {})
        result = self.collection.query(
            query_embeddings=[self.embedding_provider.embed(query)],
            n_results=top_k,
            where=where,
            include=["documents", "metadatas", "distances"],
        )
        rows = []
        for index, item_id in enumerate(result.get("ids", [[]])[0]):
            distance = result.get("distances", [[]])[0][index]
            rows.append({
                "id": item_id,
                "text": result["documents"][0][index],
                "metadata": result["metadatas"][0][index],
                "score": max(0.0, 1.0 - float(distance)),
            })
        return rows

    @staticmethod
    def _build_where(filters: dict[str, str | None]) -> dict[str, Any] | None:
        """Nội dung approved là minh bạch cho mọi người hỏi chat; access_level chỉ còn ý nghĩa
        thông tin trong metadata, không dùng để hạn chế truy xuất ở đây nữa."""
        clauses = [{"status": "approved"}]
        if filters.get("user_department"):
            clauses.append({"department": filters["user_department"]})
        if filters.get("version"):
            clauses.append({"version": filters["version"]})
        return clauses[0] if len(clauses) == 1 else {"$and": clauses}
