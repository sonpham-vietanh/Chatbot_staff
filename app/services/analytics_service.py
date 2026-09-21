from collections import Counter
from typing import Any

from app.services.supabase_client import SupabaseClient

MAX_LOGS_SCANNED = 5000
"""Chặn trần số dòng chat_logs quét mỗi lần thống kê — quy mô nội bộ vài trăm nhân viên
chưa cần phân trang/RPC riêng, gom ở tầng ứng dụng cho đơn giản, không cần thêm SQL mới."""


class AnalyticsService:
    """Thống kê nhẹ trên chat_logs: câu hỏi được hỏi nhiều nhất (khớp chữ chính xác) và
    tài liệu được trích dẫn nhiều nhất (phản ánh đúng chủ đề quan tâm hơn, không lệch
    vì cách diễn đạt câu hỏi khác nhau)."""

    def __init__(self, client: SupabaseClient):
        self.client = client

    def summary(self, limit: int = 10) -> dict[str, Any]:
        rows = self.client.select("chat_logs", {
            "select": "question,citations,grounded,created_at",
            "order": "created_at.desc",
            "limit": str(MAX_LOGS_SCANNED),
        })
        source_counter: Counter[str] = Counter()
        question_counter: Counter[str] = Counter()
        question_display: dict[str, str] = {}
        grounded_count = 0
        for row in rows:
            if row.get("grounded"):
                grounded_count += 1
            for citation in row.get("citations") or []:
                source = citation.get("source") if isinstance(citation, dict) else None
                if source:
                    source_counter[source] += 1
            question = (row.get("question") or "").strip()
            if not question:
                continue
            key = question.casefold()
            question_counter[key] += 1
            question_display.setdefault(key, question)
        return {
            "scanned_logs": len(rows),
            "grounded_count": grounded_count,
            "top_sources": [{"source": source, "count": count} for source, count in source_counter.most_common(limit)],
            "top_questions": [
                {"question": question_display[key], "count": count}
                for key, count in question_counter.most_common(limit)
            ],
        }
