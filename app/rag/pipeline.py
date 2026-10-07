import logging
import re
import unicodedata
from typing import Any

from app.config import Settings
from app.models.schemas import MAX_HISTORY_ITEMS
from app.rag.answer_format import clean_markdown, to_plain_text
from app.rag.embeddings import ZeroEmbeddingProvider, build_embedding_provider
from app.rag.lexical_store import LexicalStore
from app.rag.prompt_builder import FALLBACK_ANSWER, MISSING_DOC_RE, build_condense_prompt
from app.rag.vector_store import VectorStore
from app.services.admin_service import AdminService
from app.services.analytics_service import AnalyticsService
from app.services.api_key_service import ApiKeyService
from app.services.auth_service import AuthService
from app.services.chat_history_service import ChatHistoryService
from app.services.feedback_service import FeedbackService
from app.services.manage_service import ManageService
from app.services.ingest_agent import IngestAgent
from app.services.llm import FallbackLLMProvider, build_llm_provider
from app.services.supabase_client import SupabaseClient
from app.services.report_service import ReportKeyService, ReportService
from app.services.usage_service import UsageService
from app.services.wiki_sync_service import WikiSyncService

logger = logging.getLogger(__name__)

WEAK_LEXICAL_SCORE = 0.4
KEYWORD_PROMPT = (
    "Bạn giúp tìm tài liệu nhân sự nội bộ của một trường học ở Việt Nam. Từ câu hỏi bên dưới, viết 8–14 từ khoá hoặc cụm từ "
    "tiếng Việt (có dấu) KHÁC NHAU mà tài liệu liên quan có thể dùng: đồng nghĩa, thuật ngữ nhân sự, tên loại tài liệu "
    "(quy định, quy chế, JD mô tả công việc, hợp đồng, học bổng...). Chỉ trả về danh sách cách nhau bằng dấu phẩy, không giải thích.\n\nCâu hỏi: "
)

# Phần mở của thẻ nguồn. Mẫu chuẩn là "[Nguồn: ...]", nhưng model đôi khi viết thường/viết
# hoa, thêm khoảng trắng hoặc bọc đậm ("[**Nguồn:** ...]") — đều phải nhận ra để không lọt
# nguyên văn vào câu trả lời.
#
# Mỗi đoạn lặp trong mẫu dùng MỘT lớp ký tự duy nhất ([ \t*]*), không xếp chồng nhiều \s* cạnh
# nhau: bản trước viết "\s*\**\s*" làm regex lùi theo cấp số mũ với dòng "[" + vài trăm dấu
# cách (1 request treo 50 giây). Phần còn lại của việc tách thẻ làm bằng quét ký tự tuyến tính.
CITATION_OPEN = re.compile(r"\[[ \t*]*Nguồn[ \t*]*:[ \t*]*", re.IGNORECASE)
# Ký tự chỉ là "vỏ" còn sót sau khi gỡ thẻ: bullet, số thứ tự, đậm/nghiêng, ngoặc, dấu câu.
DECORATION_CHARS = " \t*_`.,;:-•+>()[]“”\"'"
SOURCE_LABEL_LINE = re.compile(r"^[\s*_#>-]*(?:các\s+)?nguồn[^:\n]{0,30}:[\s*_]*$", re.IGNORECASE)
CITATION_TRAILING_CHARS = " \t*_`.,;"
# Thẻ bị cắt dở ở cuối câu trả lời do hết token: "[", "[Ng", "[Nguồn"...
TRUNCATED_OPENERS = {"", "ng", "ngu", "nguồ", "nguồn"}
VERSION_PATTERN = re.compile(r"^v?\d+(\.\d+)*$", re.IGNORECASE)


class AdvancedRAGPipeline:
    def __init__(self, settings: Settings):
        self.settings = settings
        self.supabase = SupabaseClient(settings.supabase_url, settings.supabase_service_key)
        self.lexical = settings.retrieval_mode == "lexical"
        if self.lexical:
            self.embedding_provider = ZeroEmbeddingProvider(settings.embedding_dimensions)
            self.vector_store = LexicalStore(self.supabase)
        else:
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
        # Phát hiện tài liệu trùng nội dung khi upload cần điểm tương đồng ngữ nghĩa; điểm BM25 của một văn bản dài
        # luôn rất cao nên ở chế độ lexical tắt tính năng này (vẫn còn kiểm tra trùng theo tên).
        self.semantic_store = None if self.lexical else self.vector_store
        self.llm = self._build_llm(settings)
        self.admin = AdminService(self.supabase, self.embedding_provider)
        self.auth = AuthService(settings.supabase_url, settings.supabase_anon_key)
        self.chat_history = ChatHistoryService(self.supabase)
        self.analytics = AnalyticsService(self.supabase)
        self.api_keys = ApiKeyService(self.supabase)
        self.feedback = FeedbackService(self.supabase)
        self.manage = ManageService(self.admin, self.supabase, settings.admin_emails)
        self.usage = UsageService(self.supabase)
        self.report = ReportService(self.supabase)
        self.report_keys = ReportKeyService(self.supabase)
        self.ingest_agent = IngestAgent(settings) if settings.vault_path and settings.anthropic_api_key else None
        self.wiki_sync = WikiSyncService(self.admin, settings.vault_path, recorder=lambda note, kind: self.manage._record(note, kind, "wiki-sync")) if settings.vault_path else None

    @staticmethod
    def _build_llm(settings: Settings):
        """Dựng chuỗi LLM tự chuyển phương án khi cái trước lỗi (hết tiền, máy tắt...).

        - LLM_PROVIDER=openrouter: OpenRouter trước; hết thì sang endpoint Claude (nếu có LLM_BASE_URL + LLM_API_KEY):
          model chính LLM_MODEL rồi model dự phòng LLM_FALLBACK_MODEL.
        - LLM_PROVIDER=openai_compatible: endpoint Claude trước, OpenRouter (nếu có key) là dự phòng.
        Thiếu cấu hình phương án nào thì bỏ phương án đó; không còn phương án nào thì báo lỗi cấu hình rõ ràng."""
        provider = settings.llm_provider
        if provider not in ("openrouter", "openai_compatible"):
            return build_llm_provider(provider, settings.gemini_api_key, settings.gemini_model, "https://generativelanguage.googleapis.com")
        has_claude = bool(settings.llm_api_key and settings.llm_base_url)
        if provider == "openai_compatible" and not has_claude:
            # Thiếu cấu hình thì không để app chết lúc khởi động: dùng OpenRouter và kêu to trong log.
            logger.error("LLM_PROVIDER=openai_compatible nhưng thiếu LLM_API_KEY/LLM_BASE_URL — tạm dùng OpenRouter")
            provider = "openrouter"

        def openrouter() -> list:
            if not settings.openrouter_api_key:
                return []
            return [build_llm_provider("openrouter", settings.openrouter_api_key, settings.openrouter_model, settings.openrouter_base_url)]

        def claude() -> list:
            if not has_claude:
                return []
            slots = settings.llm_max_concurrency
            models = [settings.llm_model]
            if settings.llm_fallback_model and settings.llm_fallback_model != settings.llm_model:
                models.append(settings.llm_fallback_model)
            return [build_llm_provider("openai_compatible", settings.llm_api_key, model, settings.llm_base_url, slots) for model in models]

        chain = openrouter() + claude() if provider == "openrouter" else claude() + openrouter()
        if not chain:  # không còn phương án nào: để build_llm_provider báo thiếu khoá đúng tên biến
            return build_llm_provider("openrouter", settings.openrouter_api_key, settings.openrouter_model, settings.openrouter_base_url)
        llm = chain[-1]
        for backup in reversed(chain[:-1]):
            llm = FallbackLLMProvider(backup, llm)
        return llm

    def retrieve(self, question: str, history: list[dict] | None = None) -> list[dict[str, Any]]:
        """Tim kiem KHONG loc theo phong ban - moi nhan vien deu co quyen biet toan bo
        noi dung da duoc duyet vao wiki (chinh sach cua truong, khong phai gioi han ky thuat)."""
        standalone = self._standalone_question(question, history)
        results = self.vector_store.search(standalone, self.settings.top_k)
        if self.lexical and (not results or results[0]["score"] < WEAK_LEXICAL_SCORE):
            results = self._expand_and_merge(standalone, results)
        return results

    def _expand_and_merge(self, query: str, first: list[dict[str, Any]]) -> list[dict[str, Any]]:
        """Kết quả từ khoá đầu yếu (người dùng diễn đạt khác hẳn tài liệu): nhờ mô hình đưa thêm từ khoá/đồng nghĩa rồi
        tìm lại. Chỉ dùng khi cần — luôn mở rộng thì từ khoá phụ lấn át từ khoá chính và kết quả đầu tụt đi."""
        try:
            keywords = " ".join(self.llm.complete(KEYWORD_PROMPT + query, max_tokens=120).split())
        except Exception:
            return first
        if not keywords:
            return first
        merged = {item["id"]: item for item in first}
        for item in self.vector_store.search(f"{query} {keywords}", self.settings.top_k):
            if item["id"] not in merged or item["score"] > merged[item["id"]]["score"]:
                merged[item["id"]] = item
        return sorted(merged.values(), key=lambda item: -item["score"])[: self.settings.top_k]

    def _standalone_question(self, question: str, history: list[dict] | None) -> str:
        """Câu hỏi nối tiếp ("còn giáo viên thì sao?") không tự tìm được tài liệu: nhờ mô hình viết lại
        thành câu đầy đủ. Lỗi/rỗng/quá dài thì quay về cách nối chuỗi đơn giản."""
        fallback = self._retrieval_query(question, history)
        if not history:
            return question
        try:
            rewritten = " ".join(self.llm.complete(build_condense_prompt(question, history), max_tokens=150).split())
        except Exception:
            logger.warning("Không viết lại được câu hỏi nối tiếp", exc_info=True)
            return fallback
        return rewritten if 5 <= len(rewritten) <= 400 else fallback

    def search(self, question: str, department: str | None = None) -> list[dict[str, Any]]:
        """Dung cho /api/debug/search (da gate require_admin) - admin co the loc thu
        theo 1 phong ban cu the de kiem tra, khong anh huong toi chat-staff thuc te."""
        filters = {"user_department": department} if department else None
        return self.vector_store.search(question, self.settings.top_k, filters)

    def chat(self, question: str, history: list[dict] | None = None,
             asker_department: str | None = None) -> dict[str, Any]:
        history = (history or [])[-MAX_HISTORY_ITEMS:]
        results = self.retrieve(question, history)
        grounded_seeds = [item for item in results if item["score"] >= self.settings.min_relevance_score]
        # Chỉ đưa CONTEXT vào prompt khi retrieval thực sự vượt ngưỡng tin cậy; nếu không,
        # để LLM tự quyết định giữa trả lời giao tiếp thông thường hoặc từ chối theo prompt guardrail.
        context_for_llm = results if grounded_seeds else []
        # Chuẩn hoá Unicode về dạng dựng sẵn (NFC): model đôi khi trả tiếng Việt dạng tổ hợp,
        # khi đó "Nguồn"/câu từ chối không khớp chuỗi so sánh dù nhìn giống hệt.
        answer = unicodedata.normalize("NFC", self.llm.answer(question, context_for_llm, history))
        # Dòng [Nguồn: ...] luôn bị tách khỏi text hiển thị (citation trả về ở trường riêng),
        # và định dạng luôn được làm sạch ở đây — không để từng giao diện tự xử lý.
        missing_docs = bool(MISSING_DOC_RE.search(answer))
        answer = MISSING_DOC_RE.sub("", answer).strip()
        display_answer = clean_markdown(self._strip_citation_tags(answer))
        # So khớp trên văn bản thuần: model đôi khi bọc câu từ chối trong **...** hoặc kèm
        # dòng nguồn; rỗng (chỉ có dòng nguồn) cũng coi như không trả lời được.
        if to_plain_text(display_answer) in ("", FALLBACK_ANSWER) or missing_docs:
            try:
                self.admin.create_note(
                    title=f"[CẦN BỔ SUNG] {question[:150]}",  # tiêu đề tối đa 200 ký tự khi lưu ở /admin
                    department=asker_department or "Unassigned",
                    content=f"# Câu hỏi chưa có câu trả lời được duyệt\n\n{question}",
                    status="draft",
                    created_by="AI_Bot",
                )
            except Exception:
                # Note "cần bổ sung" chỉ là ghi nhận cho HR — ghi lỗi thì người hỏi vẫn phải
                # nhận được câu từ chối bình thường, không phải lỗi 502.
                logger.warning("Không tạo được note [CẦN BỔ SUNG] cho câu hỏi: %s", question, exc_info=True)
            if to_plain_text(display_answer) in ("", FALLBACK_ANSWER):
                return {"answer": FALLBACK_ANSWER, "grounded": False, "citations": []}
        citations = self._parse_citations(answer, results) if grounded_seeds else []
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
    def _split_citation_tags(answer: str) -> tuple[str, list[str]]:
        """Tách câu trả lời thành (text hiển thị không còn thẻ nguồn, nội dung thô từng thẻ).

        Mẫu chuẩn là mỗi thẻ 1 dòng riêng ở cuối, nhưng model không phải lúc nào cũng theo:
        thẻ nằm cuối câu, giữa câu, trong bullet, bọc **đậm**, thiếu ']', 2 thẻ 1 dòng...
        Chỉ gỡ đúng phần thẻ — chữ thật nằm cùng dòng phải được giữ lại."""
        def tag_end(line: str, start: int) -> int:
            """Vị trí ']' đóng thẻ, đếm ngoặc lồng nhau để heading kiểu "Mục [1]" không cắt
            thẻ giữa chừng, và "[Phụ lục 2]" đứng sau thẻ không bị nuốt vào thẻ. -1 = thiếu ']'."""
            depth = 0
            for index in range(start, len(line)):
                if line[index] == "[":
                    depth += 1
                elif line[index] == "]":
                    if depth == 0:
                        return index
                    depth -= 1
            return -1

        lines = answer.splitlines()
        kept: list[str] = []
        raws: list[str] = []
        index = 0
        while index < len(lines):
            line = lines[index]
            index += 1
            opener = CITATION_OPEN.search(line)
            if not opener:
                kept.append(line.rstrip())
                continue
            pieces: list[str] = []
            position = 0
            while opener:
                pieces.append(line[position:opener.start()].rstrip(" \t"))
                end = tag_end(line, opener.end())
                if end == -1 and index < len(lines) and "]" in lines[index] and not CITATION_OPEN.search(lines[index]):
                    # Thẻ bị xuống dòng giữa chừng ("[Nguồn:\nA > B > 0.1]"): nối dòng kế vào.
                    line = f"{line.rstrip()} {lines[index].strip()}"
                    index += 1
                    end = tag_end(line, opener.end())
                if end == -1:  # thiếu ']': thẻ chạy tới hết dòng
                    raws.append(line[opener.end():].rstrip(CITATION_TRAILING_CHARS))
                    position = len(line)
                    break
                raws.append(line[opener.end():end])
                position = end + 1
                opener = CITATION_OPEN.search(line, position)
            rest = ("".join(pieces) + line[position:]).rstrip()
            rest = rest.replace(" ()", "").replace("()", "")  # "(…[Nguồn: …])" để lại cặp ngoặc rỗng
            if not pieces[0].strip():
                rest = rest.lstrip()
            core = rest.strip(DECORATION_CHARS)
            if not core or core.isdigit():  # chỉ còn "- ", "1. ", "**", "()"... -> bỏ cả dòng
                # Nhãn giới thiệu ("Nguồn:", "Các nguồn đã dùng:") ngay trên dòng thẻ cũng bỏ theo.
                while kept and not kept[-1]:
                    kept.pop()
                if kept and SOURCE_LABEL_LINE.match(kept[-1]):
                    kept.pop()
                continue
            kept.append(rest)

        if kept:
            # Thẻ bị cắt dở do hết token ở cuối câu trả lời.
            last = kept[-1]
            bracket = last.rfind("[")
            if bracket != -1 and last[bracket + 1:].strip(" \t*").casefold() in TRUNCATED_OPENERS:
                kept[-1] = last[:bracket].rstrip()
        cleaned: list[str] = []
        for line in kept:
            if line or (cleaned and cleaned[-1]):
                cleaned.append(line)
        while cleaned and not cleaned[-1]:
            cleaned.pop()
        if raws:
            # Nhãn giới thiệu danh sách nguồn ("**Nguồn:**", "Các nguồn đã dùng:") trơ trọi ở cuối.
            while cleaned and (not cleaned[-1] or SOURCE_LABEL_LINE.match(cleaned[-1])):
                cleaned.pop()
        return "\n".join(cleaned).strip(), raws

    @staticmethod
    def _strip_citation_tags(answer: str) -> str:
        """Bỏ thẻ [Nguồn: ...] khỏi text hiển thị cho người dùng — citation đã hiển thị
        riêng ở phần Sources trên giao diện, không cần lặp lại trong câu trả lời."""
        return AdvancedRAGPipeline._split_citation_tags(answer)[0]

    @staticmethod
    def _parse_citations(answer: str, results: list[dict[str, Any]] | None = None) -> list[dict[str, str]]:
        """Lấy citation trực tiếp từ thẻ [Nguồn: ...] mà LLM thực sự trích trong câu trả lời,
        tránh gắn nhầm nguồn không liên quan (vd. câu chào hỏi trùng ngẫu nhiên với top-k).

        Heading của chunk là đường dẫn nhiều cấp nối bằng ' > ' (xem chunking.py) nên không
        tách heading/version theo vị trí được: ưu tiên khớp với metadata của chính các chunk
        đã truy xuất (`results`); không khớp thì chỉ coi phần cuối là version khi nó có dạng
        số phiên bản."""
        known: list[tuple[str, str, str, str]] = []
        for item in results or []:
            metadata = item["metadata"]
            heading = str(metadata.get("heading") or "Nội dung chung")
            known.append((
                str(metadata.get("source_file") or metadata.get("source") or ""),
                " > ".join(part.strip() for part in heading.split(">")),
                heading,
                str(metadata.get("version") or "unknown"),
            ))
        pieces: list[str] = []
        for raw in AdvancedRAGPipeline._split_citation_tags(answer)[1]:
            # "[Nguồn: A > h > 0.1; B > h > 1.0]" — 2 nguồn gộp trong 1 thẻ. Chỉ tách khi MỌI
            # phần đều kết thúc bằng số phiên bản; dấu ";" nằm trong tên mục ("Lương; thưởng")
            # thì không phải ranh giới giữa 2 nguồn.
            split = raw.split(";")
            is_merged = all(VERSION_PATTERN.match(piece.rsplit(">", 1)[-1].strip(" \t*`")) for piece in split)
            pieces.extend(split if is_merged else [raw])
        unique: dict[tuple[str, str, str], dict[str, str]] = {}
        for piece in pieces:
            parts = [part.strip(" \t*`") for part in piece.split(">")]
            if not parts or not parts[0]:
                continue
            source, rest = parts[0], [part for part in parts[1:] if part]
            cited = " > ".join(rest)
            match = next(
                (entry for entry in known if entry[0] == source
                 and cited in (entry[1], f"{entry[1]} > {entry[3]}", f"{entry[1]} > v{entry[3]}")),
                None,
            )
            if match:
                heading, version = match[2], match[3]
            elif rest and VERSION_PATTERN.match(rest[-1]):
                heading, version = " > ".join(rest[:-1]) or "Nội dung chung", rest[-1].lstrip("vV")
            else:
                heading, version = cited or "Nội dung chung", "unknown"
            citation = {"source": source, "heading": heading, "version": version}
            unique[(source, heading, version)] = citation
        return list(unique.values())
