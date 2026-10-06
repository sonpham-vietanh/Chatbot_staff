"""Tìm tài liệu theo từ khoá (BM25) — không cần mô hình embeddings, không gọi dịch vụ ngoài.

Dùng khi không có embeddings (RETRIEVAL_MODE=lexical): kho tri thức nội bộ nhỏ (vài chục tài liệu) nên toàn bộ chunk
được giữ trong bộ nhớ vài chục giây rồi tải lại. Khớp theo chữ KHÔNG DẤU (nhân viên hay gõ "nghi phep") và theo cặp từ
liền kề (tiếng Việt chủ yếu là từ ghép hai âm tiết: "nghỉ phép", "thử việc")."""
from __future__ import annotations

import math
import re
import threading
import time
import unicodedata
from collections import Counter
from typing import Any

from app.services.supabase_client import SupabaseClient

SHARED_DEPARTMENT = "Unassigned"
PAGE = 1000  # Supabase cắt mỗi truy vấn ở 1000 dòng
K1, B = 1.5, 0.75
TITLE_WEIGHT, HEADING_WEIGHT = 3, 2
SCORE_HALF = 8.0  # điểm BM25 thô = SCORE_HALF ứng với điểm chuẩn hoá 0.5

STOPWORDS = frozenset("""
va cua la co khong duoc bi thi se da dang cho toi minh ban em anh chi ai gi nao the nay do kia day
bao nhieu may nhu sao tai voi hay hoac ma nhung neu khi nen can phai de trong ngoai tren duoi tu den
mot cac nhung moi nhan vien cong ty truong hoi biet xin vui long lam sao
""".split())


def fold(text: str) -> str:
    """Chữ thường, bỏ dấu, đ -> d."""
    text = unicodedata.normalize("NFD", text.casefold().replace("đ", "d"))
    return "".join(ch for ch in text if unicodedata.category(ch) != "Mn")


def tokens(text: str, *, keep_stopwords: bool = False) -> list[str]:
    words = re.findall(r"\w+", fold(text))
    if not keep_stopwords:
        words = [w for w in words if w not in STOPWORDS and (len(w) > 1 or w.isdigit())]
    grams = [f"{a}_{b}" for a, b in zip(words, words[1:])]
    return words + grams


class LexicalStore:
    def __init__(self, client: SupabaseClient, ttl: float = 45.0):
        self.client = client
        self.ttl = ttl
        self._lock = threading.Lock()
        self._loaded_at = 0.0
        self._docs: list[dict[str, Any]] = []
        self._idf: dict[str, float] = {}
        self._avg_len = 1.0

    # ---- nạp & lập chỉ mục ----
    def _fetch_all(self, table: str, params: dict[str, str]) -> list[dict[str, Any]]:
        rows: list[dict[str, Any]] = []
        while True:
            page = self.client.select(table, {**params, "limit": str(PAGE), "offset": str(len(rows))})
            rows += page
            if len(page) < PAGE:
                return rows

    def _build(self) -> None:
        notes = {n["id"]: n for n in self._fetch_all("knowledge_notes", {
            "select": "id,title,department,access_level,version,status", "status": "eq.approved", "order": "id.asc"})}
        chunks = self._fetch_all("knowledge_chunks", {"select": "note_id,chunk_index,heading,text", "order": "note_id.asc,chunk_index.asc"})
        docs = []
        for chunk in chunks:
            note = notes.get(chunk["note_id"])
            if not note:
                continue
            title_tokens, heading_tokens = tokens(note["title"]), tokens(chunk.get("heading") or "")
            body = tokens(chunk["text"])
            weighted = Counter(body)
            for token in title_tokens:
                weighted[token] += TITLE_WEIGHT
            for token in heading_tokens:
                weighted[token] += HEADING_WEIGHT
            docs.append({"note": note, "chunk": chunk, "tf": weighted, "len": sum(weighted.values())})
        frequency: Counter[str] = Counter()
        for doc in docs:
            frequency.update(doc["tf"].keys())
        total = max(len(docs), 1)
        self._idf = {t: math.log(1 + (total - n + 0.5) / (n + 0.5)) for t, n in frequency.items()}
        self._avg_len = (sum(d["len"] for d in docs) / total) or 1.0
        self._docs = docs

    def _ensure_fresh(self) -> None:
        if time.monotonic() - self._loaded_at < self.ttl and self._docs:
            return
        with self._lock:
            if time.monotonic() - self._loaded_at < self.ttl and self._docs:
                return
            try:
                self._build()
            except Exception:
                if not self._docs:  # chưa có bản nào trong bộ nhớ thì không còn cách nào khác
                    raise
            self._loaded_at = time.monotonic()  # lỗi tạm thời thì giữ bản cũ thêm một nhịp

    # ---- tìm ----
    def search(self, query: str, top_k: int, filters: dict[str, str | None] | None = None) -> list[dict[str, Any]]:
        self._ensure_fresh()
        department = (filters or {}).get("user_department")
        terms = Counter(tokens(query))
        scored = []
        for doc in self._docs:
            note = doc["note"]
            if department and note["department"] not in (department, SHARED_DEPARTMENT):
                continue
            score = 0.0
            for term, query_count in terms.items():
                tf = doc["tf"].get(term)
                if tf:
                    score += self._idf[term] * (tf * (K1 + 1)) / (tf + K1 * (1 - B + B * doc["len"] / self._avg_len)) * (1 + 0.15 * (query_count - 1))
            if score > 0:
                scored.append((score, doc))
        scored.sort(key=lambda item: -item[0])
        results = []
        for score, doc in scored[:top_k]:
            note, chunk = doc["note"], doc["chunk"]
            results.append({
                "id": f"{note['id']}:{chunk['chunk_index']}",
                "text": chunk["text"],
                "metadata": {
                    "source": note["title"], "source_file": note["title"], "title": note["title"], "department": note["department"],
                    "heading": chunk.get("heading"), "version": note.get("version"), "access_level": note.get("access_level"), "status": "approved",
                },
                "score": score / (score + SCORE_HALF),  # 0..1 để so với MIN_RELEVANCE_SCORE
            })
        return results
