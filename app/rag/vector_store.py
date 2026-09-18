import re
from typing import Any

from app.rag.embeddings import EmbeddingProvider
from app.services.supabase_client import SupabaseClient

CANDIDATE_POOL_MULTIPLIER = 5
MAX_CANDIDATE_POOL = 60
KEYWORD_BONUS_PER_TERM = 0.05


class VectorStore:
    """Truy xuất chunk qua RPC pgvector (match_knowledge_chunks) của Supabase, rồi cộng
    thêm điểm cho chunk có từ khóa trùng khớp thật với câu hỏi trước khi cắt còn top_k —
    vault càng lớn, cosine similarity thuần càng dễ bị văn bản dài lấn át."""

    def __init__(self, client: SupabaseClient, embedding_provider: EmbeddingProvider):
        self.client = client
        self.embedding_provider = embedding_provider

    def search(self, query: str, top_k: int, filters: dict[str, str | None] | None = None) -> list[dict[str, Any]]:
        filters = filters or {}
        pool_size = min(max(top_k * CANDIDATE_POOL_MULTIPLIER, top_k), MAX_CANDIDATE_POOL)
        rows = self.client.rpc("match_knowledge_chunks", {
            "query_embedding": self.embedding_provider.embed(query),
            "match_count": pool_size,
            "filter_department": filters.get("user_department"),
        })
        query_terms = {term for term in re.findall(r"\w+", query.casefold(), flags=re.UNICODE) if len(term) >= 3}
        results = []
        for row in rows:
            cosine_score = max(0.0, 1.0 - float(row["distance"]))
            searchable = f"{row.get('title', '')} {row.get('heading', '')} {row.get('text', '')}".casefold()
            overlap = sum(term in searchable for term in query_terms)
            results.append({
                "id": f"{row['note_id']}:{row['chunk_index']}",
                "text": row["text"],
                "metadata": {
                    "source": row["title"],
                    "source_file": row["title"],
                    "title": row["title"],
                    "department": row["department"],
                    "heading": row["heading"],
                    "version": row["version"],
                    "access_level": row["access_level"],
                    "status": "approved",
                },
                "score": cosine_score + overlap * KEYWORD_BONUS_PER_TERM,
            })
        results.sort(key=lambda item: item["score"], reverse=True)
        return results[:top_k]
