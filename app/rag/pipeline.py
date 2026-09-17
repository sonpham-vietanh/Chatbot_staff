from dataclasses import dataclass
import re
from typing import Any

from app.config import Settings
from app.rag.embeddings import build_embedding_provider
from app.rag.prompt_builder import FALLBACK_ANSWER
from app.rag.reranker import NoOpReranker
from app.rag.vector_store import VectorStore
from app.services.draft_service import DraftService
from app.services.graph_service import GraphService
from app.services.llm import build_llm_provider
from app.services.obsidian import ObsidianLoader
from app.services.vault_service import VaultService


CITATION_PATTERN = re.compile(r"\[Nguồn:\s*([^>\]]+?)\s*>\s*([^>\]]+?)\s*>\s*([^\]]+?)\]")
MAX_HISTORY_TURNS = 6


@dataclass
class ReindexStats:
    indexed_files: int
    indexed_chunks: int
    graph_edges: int


class AdvancedRAGPipeline:
    def __init__(self, settings: Settings):
        self.settings = settings
        if settings.embedding_provider == "openrouter":
            embedding_api_key = settings.openrouter_api_key
            embedding_model = settings.openrouter_embedding_model
        else:
            embedding_api_key = settings.gemini_api_key
            embedding_model = settings.gemini_embedding_model
        self.embedding_provider = build_embedding_provider(
            settings.embedding_provider, embedding_api_key, embedding_model, settings.openrouter_base_url
        )
        self.vector_store = VectorStore(str(settings.vector_db_path), settings.vector_collection, self.embedding_provider)
        self.reranker = NoOpReranker()
        llm_api_key = settings.openrouter_api_key if settings.llm_provider == "openrouter" else settings.gemini_api_key
        llm_model = settings.openrouter_model if settings.llm_provider == "openrouter" else settings.gemini_model
        llm_base_url = settings.openrouter_base_url if settings.llm_provider == "openrouter" else "https://generativelanguage.googleapis.com"
        self.llm = build_llm_provider(settings.llm_provider, llm_api_key, llm_model, llm_base_url)
        self.drafts = DraftService(settings.draft_review_path)
        self.loader = ObsidianLoader(settings.obsidian_vault_path, settings.graph_path)
        self.vault = VaultService(settings.obsidian_vault_path, settings.graph_path)
        self.graph = GraphService(settings.graph_path, self.vault)

    def reindex(self) -> ReindexStats:
        chunks, indexed_files, graph_edges = self.loader.load_approved_chunks()
        self.vector_store.reset()
        self.vector_store.upsert([{"id": chunk.id, "text": chunk.text, "metadata": chunk.metadata} for chunk in chunks])
        return ReindexStats(indexed_files, len(chunks), graph_edges)

    def retrieve(self, question: str, user_department: str | None = None,
                 user_access_level: str = "staff", version: str | None = None,
                 history: list[dict] | None = None) -> list[dict[str, Any]]:
        filters = {
            "user_department": user_department,
            "user_access_level": user_access_level,
            "version": version,
        }
        seeds = self.vector_store.search(self._retrieval_query(question, history), self.settings.top_k, filters)
        for item in seeds:
            item["metadata"] = {**item.get("metadata", {}), "retrieval_stage": "vector_seed"}
        seeds = self.reranker.rerank(question, seeds)
        return self.graph.expand_context(
            seeds,
            filters,
            max_files=getattr(self.settings, "graph_max_files", 4),
            max_chunks=getattr(self.settings, "graph_max_chunks", 8),
            max_chars=getattr(self.settings, "context_max_chars", 12000),
        )

    def search(self, question: str, user_department: str | None = None,
               user_access_level: str = "staff", version: str | None = None) -> list[dict[str, Any]]:
        return self.retrieve(question, user_department, user_access_level, version)

    def chat(self, question: str, user_department: str | None = None,
             user_access_level: str = "staff", version: str | None = None,
             history: list[dict] | None = None) -> dict[str, Any]:
        history = (history or [])[-MAX_HISTORY_TURNS:]
        results = self.retrieve(question, user_department, user_access_level, version, history)
        seed_results = [item for item in results if item["metadata"].get("retrieval_stage") == "vector_seed"]
        grounded_seeds = [item for item in seed_results if item["score"] >= self.settings.min_relevance_score]
        # Chỉ đưa CONTEXT vào prompt khi retrieval thực sự vượt ngưỡng tin cậy; nếu không,
        # để LLM tự quyết định giữa trả lời giao tiếp thông thường hoặc từ chối theo prompt guardrail.
        context_for_llm = results if grounded_seeds else []
        answer = self.llm.answer(question, context_for_llm, history)
        if answer.strip() == FALLBACK_ANSWER:
            draft_path = self.drafts.create_unanswered_draft(question, user_department)
            return {
                "answer": FALLBACK_ANSWER,
                "grounded": False,
                "citations": [],
                "draft_created": True,
                "draft_path": str(draft_path),
            }
        citations = self._parse_citations(answer) if grounded_seeds else []
        return {"answer": answer, "grounded": bool(citations), "citations": citations, "draft_created": False, "draft_path": None}

    @staticmethod
    def _retrieval_query(question: str, history: list[dict] | None) -> str:
        """Nối câu hỏi trước đó của user vào query embedding, để câu hỏi nối tiếp kiểu
        'vậy đang ở bậc 1 thì...' vẫn tìm đúng chunk dù thiếu từ khóa gốc."""
        if not history:
            return question
        last_user_turn = next((turn["content"] for turn in reversed(history) if turn["role"] == "user"), None)
        return f"{last_user_turn} {question}" if last_user_turn else question

    @staticmethod
    def _parse_citations(answer: str) -> list[dict[str, str]]:
        """Lấy citation trực tiếp từ tag [Nguồn: ...] mà LLM thực sự trích trong câu trả lời,
        tránh gắn nhầm nguồn không liên quan (vd. câu chào hỏi trùng ngẫu nhiên với top-k)."""
        unique: dict[tuple[str, str, str], dict[str, str]] = {}
        for source, heading, version in CITATION_PATTERN.findall(answer):
            citation = {"source": source.strip(), "heading": heading.strip(), "version": version.strip()}
            unique[(citation["source"], citation["heading"], citation["version"])] = citation
        return list(unique.values())
