import re
from typing import Any

from app.rag.embeddings import EmbeddingProvider
from app.services.supabase_client import SupabaseClient

CANDIDATE_POOL_MULTIPLIER = 5
MAX_CANDIDATE_POOL = 60
KEYWORD_BONUS_PER_TERM = 0.05
SHARED_DEPARTMENT = "Unassigned"
"""department cua noi dung dung chung (core/ trong vault) - xem _department_for_path
trong wiki_sync_service.py. Loc theo 1 phong ban cu the KHONG duoc lam mat noi dung
nay, nen search() luon gom them rieng, khong chi dua vao filter_department cua RPC."""


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
        department = filters.get("user_department")
        embedding = self.embedding_provider.embed(query)
        rows = self.client.rpc("match_knowledge_chunks", {
            "query_embedding": embedding,
            "match_count": pool_size,
            "filter_department": department,
        })
        if department and department != SHARED_DEPARTMENT:
            # filter_department cua RPC so khop tuyet doi nen tu no se loai mat noi
            # dung dung chung (core/) - goi rieng 1 lan nua de dam bao khong bi mat.
            shared_rows = self.client.rpc("match_knowledge_chunks", {
                "query_embedding": embedding,
                "match_count": pool_size,
                "filter_department": SHARED_DEPARTMENT,
            })
            seen = {(r["note_id"], r["chunk_index"]) for r in rows}
            rows = rows + [r for r in shared_rows if (r["note_id"], r["chunk_index"]) not in seen]
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
