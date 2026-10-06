"""Tìm tài liệu theo từ khoá (RETRIEVAL_MODE=lexical): khớp không dấu, ưu tiên tiêu đề, lọc phòng ban, làm mới dần,
mở rộng từ khoá khi kết quả yếu, và không đụng tới embeddings."""
from types import SimpleNamespace

import pytest

from app.rag.embeddings import ZeroEmbeddingProvider
from app.rag.lexical_store import LexicalStore, fold, tokens
from app.rag.pipeline import AdvancedRAGPipeline


class FakeClient:
    def __init__(self, notes, chunks):
        self.tables = {"knowledge_notes": notes, "knowledge_chunks": chunks}
        self.calls = 0
        self.fail = False

    def select(self, table, params=None):
        if self.fail:
            raise RuntimeError("db down")
        self.calls += 1
        params = params or {}
        rows = list(self.tables[table])
        if "status" in params:
            rows = [r for r in rows if r.get("status") == params["status"][3:]]
        offset, limit = int(params.get("offset", 0)), int(params.get("limit", 1000))
        return rows[offset: offset + limit]


def corpus():
    notes = [
        {"id": "n1", "title": "Quy định nghỉ phép và trả phép", "department": "HR", "access_level": "staff", "version": "0.1", "status": "approved"},
        {"id": "n2", "title": "Quy chế lương thưởng", "department": "HR", "access_level": "staff", "version": "0.2", "status": "approved"},
        {"id": "n3", "title": "Quy trình mua sắm", "department": "Finance", "access_level": "staff", "version": "0.1", "status": "approved"},
        {"id": "n4", "title": "Bản nháp chưa duyệt", "department": "HR", "access_level": "staff", "version": "0.1", "status": "draft"},
    ]
    chunks = [
        {"note_id": "n1", "chunk_index": 0, "heading": "Số ngày phép", "text": "Nhân viên được nghỉ phép năm 12 ngày hưởng lương mỗi năm."},
        {"note_id": "n1", "chunk_index": 1, "heading": "Xin nghỉ ốm", "text": "Nghỉ ốm đột xuất phải gửi email cho Leader và cc HR."},
        {"note_id": "n2", "chunk_index": 0, "heading": "Thưởng Tết", "text": "Thưởng Tết Âm lịch tối thiểu 1.000.000 đồng cho người làm từ 1 năm."},
        {"note_id": "n3", "chunk_index": 0, "heading": "Đề xuất mua", "text": "Mọi khoản mua sắm trên 5 triệu cần kế toán trưởng phê duyệt."},
        {"note_id": "n4", "chunk_index": 0, "heading": "Nháp", "text": "Nghỉ phép bí mật chưa công bố."},
    ]
    return notes, chunks


def test_fold_and_tokens_ignore_accents_stopwords_and_make_bigrams():
    assert fold("Đi Muộn, NGHỈ PHÉP") == "di muon, nghi phep"
    toks = tokens("Nghỉ phép năm được bao nhiêu ngày")
    assert "nghi_phep" in toks and "phep_nam" in toks and "bao" not in toks and "duoc" not in toks


def test_search_matches_without_accents_and_ranks_the_right_note_first():
    store = LexicalStore(FakeClient(*corpus()))
    for query in ("nghỉ phép năm được bao nhiêu ngày", "nghi phep nam", "xin nghỉ ốm thế nào"):
        top = store.search(query, 5)[0]
        assert top["metadata"]["title"] == "Quy định nghỉ phép và trả phép", query
    assert store.search("thưởng tết", 3)[0]["metadata"]["title"] == "Quy chế lương thưởng"
    assert store.search("kế toán trưởng phê duyệt", 3)[0]["metadata"]["title"] == "Quy trình mua sắm"


def test_results_have_the_same_shape_as_vector_search_and_scores_in_unit_range():
    result = LexicalStore(FakeClient(*corpus())).search("nghỉ phép", 3)[0]
    assert set(result) == {"id", "text", "metadata", "score"} and 0 < result["score"] < 1
    assert result["id"] == "n1:0" or result["id"].startswith("n1:")
    assert {"source", "source_file", "title", "department", "heading", "version", "access_level", "status"} <= set(result["metadata"])


def test_draft_notes_never_appear_and_unrelated_queries_return_nothing():
    store = LexicalStore(FakeClient(*corpus()))
    assert all(r["metadata"]["title"] != "Bản nháp chưa duyệt" for r in store.search("nghỉ phép bí mật", 10))
    assert store.search("xin chào bạn là ai", 5) == []


def test_department_filter_keeps_shared_unassigned_content():
    notes, chunks = corpus()
    notes.append({"id": "n5", "title": "Cơ cấu tổ chức", "department": "Unassigned", "access_level": "staff", "version": "0.1", "status": "approved"})
    chunks.append({"note_id": "n5", "chunk_index": 0, "heading": "Sơ đồ", "text": "Sơ đồ cơ cấu tổ chức công ty và phòng ban mua sắm."})
    store = LexicalStore(FakeClient(notes, chunks))
    titles = {r["metadata"]["title"] for r in store.search("mua sắm", 10, {"user_department": "HR"})}
    assert "Quy trình mua sắm" not in titles and "Cơ cấu tổ chức" in titles


def test_index_is_cached_refreshed_after_ttl_and_survives_a_database_blip():
    client = FakeClient(*corpus())
    store = LexicalStore(client, ttl=60)
    store.search("nghỉ phép", 3)
    loads = client.calls
    store.search("thưởng", 3)
    assert client.calls == loads  # trong thời gian nghỉ không tải lại
    store._loaded_at = 0  # hết hạn
    client.fail = True
    assert store.search("nghỉ phép", 3)  # DB lỗi thoáng qua: vẫn trả bằng bản cũ trong bộ nhớ
    client.fail = False
    client.tables["knowledge_chunks"].append({"note_id": "n3", "chunk_index": 1, "heading": "Mới", "text": "Quy định công tác phí mới."})
    store._loaded_at = 0
    assert any("công tác" in r["text"] for r in store.search("công tác phí", 3))


def test_loading_pages_through_more_than_one_thousand_chunks():
    notes = [{"id": "n1", "title": "Tài liệu lớn", "department": "HR", "access_level": "staff", "version": "0.1", "status": "approved"}]
    chunks = [{"note_id": "n1", "chunk_index": i, "heading": f"Mục {i}", "text": f"nội dung số {i} về quy trình"} for i in range(2300)]
    store = LexicalStore(FakeClient(notes, chunks))
    store.search("quy trình", 3)
    assert len(store._docs) == 2300


# ---------- nối vào pipeline ----------
def make_pipeline(client, llm):
    rag = AdvancedRAGPipeline.__new__(AdvancedRAGPipeline)
    rag.settings = SimpleNamespace(top_k=5, min_relevance_score=0.22)
    rag.lexical = True
    rag.vector_store = LexicalStore(client)
    rag.llm = llm
    return rag


class StubLLM:
    def __init__(self, reply="nghỉ phép năm, ngày phép"):
        self.reply, self.calls = reply, []

    def complete(self, prompt, max_tokens=200):
        self.calls.append(prompt)
        return self.reply


def test_keyword_expansion_runs_only_when_the_first_results_are_weak():
    llm = StubLLM()
    rag = make_pipeline(FakeClient(*corpus()), llm)
    assert rag.retrieve("nghỉ phép năm", None)[0]["metadata"]["title"] == "Quy định nghỉ phép và trả phép"
    assert llm.calls == []  # kết quả đầu đã tốt: không tốn thêm một lời gọi mô hình
    weak = rag.retrieve("ngày nghỉ hằng năm", None)  # diễn đạt khác tài liệu
    assert len(llm.calls) == 1 and weak and weak[0]["metadata"]["title"] == "Quy định nghỉ phép và trả phép"


def test_expansion_failure_falls_back_to_the_first_results():
    class Broken:
        def complete(self, prompt, max_tokens=200):
            raise RuntimeError("endpoint tắt")

    rag = make_pipeline(FakeClient(*corpus()), Broken())
    assert rag.retrieve("ngày nghỉ hằng năm", None) == rag.vector_store.search("ngày nghỉ hằng năm", 5)


def test_lexical_mode_builds_without_any_embedding_key_and_writes_zero_vectors(monkeypatch):
    settings = SimpleNamespace(
        retrieval_mode="lexical", embedding_provider="openrouter", openrouter_api_key=None, embedding_dimensions=1536,
        supabase_url="https://x.supabase.co", supabase_service_key="k", supabase_anon_key="a", llm_provider="mock", admin_emails="",
        vault_path=None, anthropic_api_key=None, gemini_api_key=None, gemini_model="g", top_k=5, min_relevance_score=0.22)
    rag = AdvancedRAGPipeline(settings)
    assert rag.lexical and isinstance(rag.embedding_provider, ZeroEmbeddingProvider) and rag.semantic_store is None
    assert rag.embedding_provider.embed_batch(["a", "b"]) == [[0.0] * 1536, [0.0] * 1536]


def test_reindex_script_refuses_to_run_in_lexical_mode(monkeypatch, capsys):
    import importlib.util
    from pathlib import Path

    spec = importlib.util.spec_from_file_location("reindex", Path(__file__).parent.parent / "scripts" / "reindex_embeddings.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    monkeypatch.setattr(module, "get_settings", lambda: SimpleNamespace(retrieval_mode="lexical"))
    assert module.main() == 2 and "lexical" in capsys.readouterr().out
