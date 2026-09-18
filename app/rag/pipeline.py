from dataclasses import dataclass
import re
from threading import Lock
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


CITATION_LINE_PATTERN = re.compile(r"^\s*\[Nguồn:\s*(.+?)\]?\s*$", re.MULTILINE)
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
        self._reindex_lock = Lock()

    def reindex(self) -> ReindexStats:
        """Khoá để 2 lần reindex (vd watcher tự động + nút 'Đồng bộ AI' thủ công bấm gần
        như cùng lúc) không bao giờ chạy chồng nhau — reset() của lần sau có thể xoá mất
        dữ liệu lần trước đang upsert dở, gây vector store rỗng ngẫu nhiên."""
        with self._reindex_lock:
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
        display_answer = self._strip_citation_tags(answer) if citations else answer
        return {"answer": display_answer, "grounded": bool(citations), "citations": citations, "draft_created": False, "draft_path": None}

    @staticmethod
    def _retrieval_query(question: str, history: list[dict] | None) -> str:
        """Nối câu hỏi trước đó của user vào query embedding, để câu hỏi nối tiếp kiểu
        'vậy đang ở bậc 1 thì...' vẫn tìm đúng chunk dù thiếu từ khóa gốc."""
        if not history:
            return question
        last_user_turn = next((turn["content"] for turn in reversed(history) if turn["role"] == "user"), None)
        return f"{last_user_turn} {question}" if last_user_turn else question

    @staticmethod
    def _strip_citation_tags(answer: str) -> str:
        """Bỏ dòng [Nguồn: ...] khỏi text hiển thị cho người dùng — citation đã hiển thị
        riêng ở phần Sources trên giao diện, không cần lặp lại trong câu trả lời."""
        without_tags = CITATION_LINE_PATTERN.sub("", answer)
        lines = [line.rstrip() for line in without_tags.splitlines()]
        cleaned: list[str] = []
        for line in lines:
            if line or (cleaned and cleaned[-1]):
                cleaned.append(line)
        return "\n".join(cleaned).strip()

    @staticmethod
    def _parse_citations(answer: str) -> list[dict[str, str]]:
        """Lấy citation trực tiếp từ dòng [Nguồn: ...] mà LLM thực sự trích trong câu trả lời,
        tránh gắn nhầm nguồn không liên quan (vd. câu chào hỏi trùng ngẫu nhiên với top-k).
        Parse theo dòng, không bắt buộc dấu ']' đóng chuẩn vì model đôi khi bỏ sót."""
        unique: dict[tuple[str, str, str], dict[str, str]] = {}
        for raw in CITATION_LINE_PATTERN.findall(answer):
            parts = [part.strip() for part in raw.split(">")]
            if not parts or not parts[0]:
                continue
            source = parts[0]
            heading = parts[1] if len(parts) > 1 else "Nội dung chung"
            version = " > ".join(parts[2:]) if len(parts) > 2 else "unknown"
            citation = {"source": source, "heading": heading, "version": version}
            unique[(source, heading, version)] = citation
        return list(unique.values())
