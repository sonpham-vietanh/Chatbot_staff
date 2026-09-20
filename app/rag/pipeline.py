import re
from typing import Any

from app.config import Settings
from app.rag.embeddings import build_embedding_provider
from app.rag.prompt_builder import FALLBACK_ANSWER
from app.rag.vector_store import VectorStore
from app.services.admin_service import AdminService
from app.services.auth_service import AuthService
from app.services.chat_history_service import ChatHistoryService
from app.services.llm import build_llm_provider
from app.services.supabase_client import SupabaseClient

CITATION_LINE_PATTERN = re.compile(r"^\s*\[Nguồn:\s*(.+?)\]?\s*$", re.MULTILINE)
MAX_HISTORY_TURNS = 6


class AdvancedRAGPipeline:
    def __init__(self, settings: Settings):
        self.settings = settings
        self.supabase = SupabaseClient(settings.supabase_url, settings.supabase_service_key)
        if settings.embedding_provider == "openrouter":
            embedding_api_key = settings.openrouter_api_key
            embedding_model = settings.openrouter_embedding_model
        else:
            embedding_api_key = settings.gemini_api_key
            embedding_model = settings.gemini_embedding_model
        self.embedding_provider = build_embedding_provider(
            settings.embedding_provider, embedding_api_key, embedding_model, settings.openrouter_base_url
        )
        self.vector_store = VectorStore(self.supabase, self.embedding_provider)
        llm_api_key = settings.openrouter_api_key if settings.llm_provider == "openrouter" else settings.gemini_api_key
        llm_model = settings.openrouter_model if settings.llm_provider == "openrouter" else settings.gemini_model
        llm_base_url = settings.openrouter_base_url if settings.llm_provider == "openrouter" else "https://generativelanguage.googleapis.com"
        self.llm = build_llm_provider(settings.llm_provider, llm_api_key, llm_model, llm_base_url)
        self.admin = AdminService(self.supabase, self.embedding_provider)
        self.auth = AuthService(settings.supabase_url, settings.supabase_service_key)
        self.chat_history = ChatHistoryService(self.supabase)

    def retrieve(self, question: str, user_department: str | None = None,
                 history: list[dict] | None = None) -> list[dict[str, Any]]:
        filters = {"user_department": user_department}
        return self.vector_store.search(self._retrieval_query(question, history), self.settings.top_k, filters)

    def search(self, question: str, user_department: str | None = None) -> list[dict[str, Any]]:
        return self.retrieve(question, user_department)

    def chat(self, question: str, user_department: str | None = None,
             history: list[dict] | None = None) -> dict[str, Any]:
        history = (history or [])[-MAX_HISTORY_TURNS:]
        results = self.retrieve(question, user_department, history)
        grounded_seeds = [item for item in results if item["score"] >= self.settings.min_relevance_score]
        # Chỉ đưa CONTEXT vào prompt khi retrieval thực sự vượt ngưỡng tin cậy; nếu không,
        # để LLM tự quyết định giữa trả lời giao tiếp thông thường hoặc từ chối theo prompt guardrail.
        context_for_llm = results if grounded_seeds else []
        answer = self.llm.answer(question, context_for_llm, history)
        if answer.strip() == FALLBACK_ANSWER:
            self.admin.create_note(
                title=f"[CẦN BỔ SUNG] {question}",
                department=user_department or "Unassigned",
                content=f"# Câu hỏi chưa có câu trả lời được duyệt\n\n{question}",
                status="draft",
                created_by="AI_Bot",
            )
            return {"answer": FALLBACK_ANSWER, "grounded": False, "citations": []}
        citations = self._parse_citations(answer) if grounded_seeds else []
        display_answer = self._strip_citation_tags(answer) if citations else answer
        return {"answer": display_answer, "grounded": bool(citations), "citations": citations}

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
