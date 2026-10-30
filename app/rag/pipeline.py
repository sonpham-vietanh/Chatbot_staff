import difflib
import logging
import re
import threading
import unicodedata
from datetime import datetime
from typing import Any

from app.config import Settings
from app.models.schemas import MAX_HISTORY_ITEMS
from app.rag.answer_format import clean_markdown, to_plain_text
from app.rag.embeddings import ZeroEmbeddingProvider, build_embedding_provider
from app.rag.lexical_store import LexicalStore, fold
from app.rag.prompt_builder import (
    FALLBACK_ANSWER, MISSING_DOC_RE, OFF_TOPIC_ANSWER, VN_TZ, WEEKDAYS, build_condense_prompt,
)
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
from app.services.points_service import PointsService
from app.services.change_request_service import ChangeRequestService
from app.services.directory_service import DirectoryService
from app.services.profile_service import ProfileService
from app.services.wig_service import WigService
from app.services.usage_service import UsageService
from app.services.webhook_service import WebhookService
from app.services.wiki_sync_service import WikiSyncService

logger = logging.getLogger(__name__)

WEAK_LEXICAL_SCORE = 0.4
_FILLER = r"(?:\s+(?:may|bao nhieu|nao|gi|the nao|vay|a|nhi|nhe|nha|roi|ha))*"
_END = r"[\s,!.?]*$"
NO_DOCUMENT_QUESTION = re.compile(
    rf"^\s*(?:(?:hom nay|hien nay|bay gio|hien tai)\s+(?:la\s+)?(?:ngay|thu|thang|nam|may gio){_FILLER}{_END}"
    rf"|(?:xin chao|chao|hello|hi|alo)(?:\s+(?:ban|bot|tro ly|em|anh|chi|ad|admin))?{_END}"
    rf"|(?:cam on|thanks|thank you|ok|oke)(?:\s+(?:ban|bot|nhe|nha|nhieu|rat nhieu))*{_END})")
"""Chỉ khớp ĐÚNG câu ngày giờ / chào hỏi / cảm ơn (đã bỏ dấu, không kèm nội dung khác): kết quả từ khoá yếu là bình thường,
không cần nhờ mô hình mở rộng từ khoá. Câu có thêm nội dung ("chào, nghỉ phép mấy ngày") không khớp nên vẫn được mở rộng."""
GREETING_RE = re.compile(rf"^\s*(?:xin chao|chao|hello|hi|alo)(?:\s+(?:ban|bot|tro ly|em|anh|chi|ad|admin))?{_END}")
ABOUT_BOT_RE = re.compile(
    rf"^\s*(?:(?:ban|bot|tro ly|ad)\s+(?:la\s+)?(?:ai|gi)(?:\s+(?:vay|the|day))?"
    rf"|(?:ban|bot|tro ly)\s+(?:lam duoc gi|giup duoc gi|co the lam gi|co the giup gi|ten gi|ten la gi)"
    rf"|ai\s+(?:day|vay)){_FILLER}{_END}")
"""Hỏi "bạn là ai / bot là gì / bạn làm được gì": trả lời giới thiệu ngay, không qua mô hình."""
THANKS_RE = re.compile(rf"^\s*(?:cam on|thanks|thank you|ok|oke)(?:\s+(?:ban|bot|nhe|nha|nhieu|rat nhieu))*{_END}")
DATE_RE = re.compile(rf"^\s*(?:hom nay|hien nay|bay gio|hien tai)\s+(?:la\s+)?(?:ngay|thu|thang|nam|may gio){_FILLER}{_END}")
"""Chào hỏi / cảm ơn / hỏi ngày giờ trả lời ngay, không gọi mô hình: nhanh (không xếp hàng sau các câu hỏi nặng) và
không bao giờ tạo note "cần bổ sung"."""
JUNK_QUESTION = re.compile(
    r"ignore (?:all |the )?(?:previous|above)|system prompt|api[\s_-]?key|mat khau|password|drop table|<script|bo qua (?:moi )?huong dan",
    re.IGNORECASE)
"""Câu dò prompt / tấn công / rác: không đáng để HR bổ sung tài liệu nên không tạo note nháp."""
SYNONYM_RULES = (
    (re.compile(r"\b(?:luong )?thang 13\b"), "thưởng Tết thưởng cuối năm"),
    (re.compile(r"\bbhxh\b|\bbao hiem\b"), "bảo hiểm xã hội BHXH thử việc hợp đồng lao động chính thức"),
    (re.compile(r"\b(?:bao ai|bao cho ai|xin nghi (?:1|mot) ngay)\b"), "xin nghỉ phép email Leader cc HR trước 48 giờ"),
    (re.compile(r"\b(?:bao nhieu|may|nhung|cac|nao) co so\b|\bco so nao\b"), "các cơ sở địa chỉ cơ sở Việt Anh Phan Huy Ích Nguyễn Trọng Tuyển Bình Tân"),
    (re.compile(r"\bnotice period\b|\bresign"), "báo trước nghỉ việc 45 ngày 30 ngày 3 ngày làm việc hợp đồng"),
    (re.compile(r"\bannual leave\b|\bleave days\b"), "nghỉ phép năm số ngày phép thâm niên"),
    (re.compile(r"\bmaternity\b|\bparental\b"), "thai sản sinh con nghỉ"),
    (re.compile(r"\bprobation"), "thử việc 85% lương"),
    (re.compile(r"\bsalary\b|\bbonus\b"), "lương thưởng"),
    # Viết tắt / cách nói thường. Chữ đã bỏ dấu nên "hđ" = "hd"; "cưới" chỉ khớp khi đi với "nghỉ/đi/xin/đám" để không bắt nhầm "cuối năm", "nghỉ cuối tuần".
    (re.compile(r"\bhd\b|\bhdld\b"), "hợp đồng lao động"),
    (re.compile(r"\bot\b"), "overtime làm thêm giờ"),
    (re.compile(r"\bgt\b"), "giám thị giáo viên"),
    (re.compile(r"\bgv\b"), "giáo viên"),
    (re.compile(r"\bvbi\b|\bdam cuoi\b|\bhon le\b|\b(?:nghi|di|xin) cuoi\b(?! (?:tuan|nam|thang|ky|cung))|^cuoi\b(?! (?:tuan|nam|thang|ky|cung))"),
     "nghỉ kết hôn 3 ngày hôn lễ bảo hiểm VBI"),
    (re.compile(r"\bthu viec bao lau\b|\bthoi gian thu viec\b"), "thời gian thử việc hợp đồng thử việc Advisory đánh giá kết quả thử việc"),
)
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
        self.webhook = WebhookService(getattr(settings, "os_webhook_url", None), getattr(settings, "os_webhook_secret", None))
        self.usage = UsageService(self.supabase, self.webhook)
        self.report = ReportService(self.supabase)
        self.report_keys = ReportKeyService(self.supabase)
        self.profiles = ProfileService(self.supabase)
        self.directory = DirectoryService(self.supabase, getattr(settings, "allowed_email_domains", ""))
        self.change_requests = ChangeRequestService(self.supabase, self.directory)
        self.wigs = WigService(self.supabase, settings.admin_emails)
        self.points = PointsService(self.supabase)
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
        return self._retrieve_standalone(self._standalone_question(question, history))

    def _retrieve_standalone(self, standalone: str) -> list[dict[str, Any]]:
        standalone = self._with_synonyms(standalone)
        results = self.vector_store.search(standalone, self.settings.top_k)
        if self.lexical and (not results or results[0]["score"] < WEAK_LEXICAL_SCORE) and not NO_DOCUMENT_QUESTION.match(fold(standalone)):
            results = self._expand_and_merge(standalone, results)
        return results

    @staticmethod
    def _with_synonyms(query: str) -> str:
        """Bổ sung từ khoá đồng nghĩa cho cách hỏi thường gặp mà tài liệu diễn đạt khác (nhân viên hỏi "lương tháng 13" nhưng tài liệu gọi là
        thưởng Tết / thưởng cuối năm; hỏi tiếng Anh...). Chỉ thêm khi khớp, nên câu bình thường không bị thay đổi."""
        folded = fold(query)
        extra = [words for pattern, words in SYNONYM_RULES if pattern.search(folded)]
        return f"{query} {' '.join(extra)}" if extra else query

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

    @staticmethod
    def _quick_reply(question: str) -> str | None:
        """Trả lời tức thì cho chào hỏi / cảm ơn / hỏi ngày giờ (xem GREETING_RE)."""
        folded = fold(question)
        if GREETING_RE.match(folded):
            return ("Chào bạn! Mình là trợ lý nội bộ của Trường Việt Anh. Bạn cần hỏi về nhân sự, chính sách, lương thưởng, "
                    "nghỉ phép hay WIG của mình thì cứ nhắn nhé.")
        if THANKS_RE.match(folded):
            return "Không có gì! Cần hỏi thêm gì bạn cứ nhắn mình nhé."
        if DATE_RE.match(folded):
            now = datetime.now(VN_TZ)
            today = f"Hôm nay là {WEEKDAYS[now.weekday()]}, ngày {now.day:02d}/{now.month:02d}/{now.year}."
            if "gio" in folded:
                return f"Mình không xem được đồng hồ nên chưa biết giờ hiện tại. {today}"
            return today
        if ABOUT_BOT_RE.match(folded):
            return ("Mình là trợ lý nội bộ của Trường Việt Anh. Mình trả lời nhanh các câu hỏi về chính sách và quy định của trường, "
                    "nhân sự, lương thưởng, nghỉ phép, WIG và hồ sơ của bạn dựa trên tài liệu đã được HR duyệt. Bạn cứ hỏi thử nhé.")
        return None

    def _llm_inputs(self, question: str, history: list[dict] | None) -> tuple[list[dict], list[dict], list[dict]]:
        """Phần tìm tài liệu dùng chung cho chat() và chat_stream(): (history cắt gọn, kết quả tìm, đoạn vượt ngưỡng)."""
        history = (history or [])[-MAX_HISTORY_ITEMS:]
        results = self.retrieve(question, history)
        grounded_seeds = [item for item in results if item["score"] >= self.settings.min_relevance_score]
        return history, results, grounded_seeds

    def chat(self, question: str, history: list[dict] | None = None,
             asker_department: str | None = None, asker: str | None = None) -> dict[str, Any]:
        quick = self._quick_reply(question)
        if quick is not None:
            return {"answer": quick, "grounded": False, "citations": []}
        history, results, grounded_seeds = self._llm_inputs(question, history)
        # Chỉ đưa vào prompt đúng những đoạn đã vượt ngưỡng tin cậy (không phải cả top_k=20) —
        # đoạn dưới ngưỡng vốn đã bị hệ thống coi là "chưa đủ liên quan" ở chỗ khác (grounded_seeds),
        # đưa cả vào prompt chỉ làm prompt dài hơn (chậm sinh câu trả lời) mà không thêm độ tin cậy.
        raw_answer = self.llm.answer(question, grounded_seeds, history, asker) if asker else self.llm.answer(question, grounded_seeds, history)
        return self._finish_answer(question, history, asker_department, results, grounded_seeds, raw_answer)

    def chat_stream(self, question: str, history: list[dict] | None = None,
                    asker_department: str | None = None, asker: str | None = None):
        """Như chat() nhưng sinh dần: yield ("delta", chữ thô) rồi cuối cùng ("result", dict) giống hệt chat().
        Lỗi LLM trước token đầu đã được lớp provider chuyển sang dự phòng; lỗi sau khi đã có chữ thì ném ra cho lớp API."""
        quick = self._quick_reply(question)
        if quick is not None:
            yield "result", {"answer": quick, "grounded": False, "citations": []}
            return
        history, results, grounded_seeds = self._llm_inputs(question, history)
        stream = getattr(self.llm, "answer_stream", None)
        if stream is None:  # provider cũ/test không có stream: một cục
            raw_answer = self.llm.answer(question, grounded_seeds, history, asker) if asker else self.llm.answer(question, grounded_seeds, history)
            yield "delta", raw_answer
        else:
            parts: list[str] = []
            for piece in (stream(question, grounded_seeds, history, asker) if asker else stream(question, grounded_seeds, history)):
                parts.append(piece)
                yield "delta", piece
            raw_answer = "".join(parts).strip() or FALLBACK_ANSWER  # giống llm.answer(): rỗng -> câu từ chối chuẩn
        yield "result", self._finish_answer(question, history, asker_department, results, grounded_seeds, raw_answer)

    def _finish_answer(self, question: str, history: list[dict], asker_department: str | None,
                       results: list[dict], grounded_seeds: list[dict], raw_answer: str) -> dict[str, Any]:
        # Chuẩn hoá Unicode về dạng dựng sẵn (NFC): model đôi khi trả tiếng Việt dạng tổ hợp,
        # khi đó "Nguồn"/câu từ chối không khớp chuỗi so sánh dù nhìn giống hệt.
        answer = unicodedata.normalize("NFC", raw_answer)
        # Dòng [Nguồn: ...] luôn bị tách khỏi text hiển thị (citation trả về ở trường riêng),
        # và định dạng luôn được làm sạch ở đây — không để từng giao diện tự xử lý.
        missing_docs = bool(MISSING_DOC_RE.search(answer))
        answer = MISSING_DOC_RE.sub("", answer).strip()
        display_answer = clean_markdown(self._strip_citation_tags(answer))
        # So khớp trên văn bản thuần: model đôi khi bọc câu từ chối trong **...** hoặc kèm
        # dòng nguồn; rỗng (chỉ có dòng nguồn) cũng coi như không trả lời được.
        plain = to_plain_text(display_answer)
        if to_plain_text(OFF_TOPIC_ANSWER)[:60] in plain:
            # Câu ngoài chủ đề: từ chối nhẹ nhàng (có thể kèm 1–2 câu đồng cảm ở đầu), KHÔNG tạo note nháp cho HR.
            return {"answer": display_answer, "grounded": False, "citations": []}
        is_fallback = plain in ("", FALLBACK_ANSWER)
        citations = self._parse_citations(answer, results) if grounded_seeds else []
        # Chỉ báo HR bổ sung tài liệu khi thật sự thiếu: không có câu trả lời, hoặc mô hình báo thiếu mà không dẫn được nguồn nào.
        # Đã trả lời được bằng tài liệu (dù thiếu chi tiết phụ) thì không tạo note để hàng đợi HR không bị nhiễu.
        if (is_fallback or (missing_docs and not citations)) and self._worth_recording(question):
            self._spawn(self._record_missing_doc, question, history, asker_department)
        if is_fallback:
            return {"answer": FALLBACK_ANSWER, "grounded": False, "citations": []}
        return {"answer": display_answer, "grounded": bool(citations), "citations": citations}

    @staticmethod
    def _spawn(target, *args) -> None:
        """Chạy việc phụ ở luồng nền (tách riêng để test chạy đồng bộ được)."""
        threading.Thread(target=target, args=args, daemon=True).start()

    @staticmethod
    def _worth_recording(question: str) -> bool:
        folded = fold(question)
        return len(folded) >= 8 and len(folded.split()) >= 2 and not JUNK_QUESTION.search(folded)             and not NO_DOCUMENT_QUESTION.match(folded)

    def _record_missing_doc(self, question: str, history: list[dict] | None, asker_department: str | None) -> None:
        """Chạy ở luồng nền (xem chat()) — lỗi ghi chỉ cần log lại cho HR biết mà sửa,
        không có đường nào trả lỗi về cho người hỏi vì câu trả lời đã trả về rồi."""
        try:
            # Câu nối tiếp ("Còn giám thị?") phải được viết lại đầy đủ, nếu không HR đọc không hiểu hỏi gì.
            question = self._standalone_question(question, history)
            title = f"[CẦN BỔ SUNG] {question[:150]}"  # tiêu đề tối đa 200 ký tự khi lưu ở /admin
            # Cùng một câu hỏi được hỏi nhiều lần chỉ cần một bản nháp.
            existing = self.supabase.select("knowledge_notes", {"select": "id", "title": f"eq.{title}", "limit": "1"})
            if existing:
                return
            self.admin.create_note(
                title=title,
                department=asker_department or "Unassigned",
                content=f"# Câu hỏi chưa có câu trả lời được duyệt\n\n{question}",
                status="draft",
                created_by="AI_Bot",
            )
        except Exception:
            logger.warning("Không tạo được note [CẦN BỔ SUNG] cho câu hỏi: %s", question, exc_info=True)

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
            known_sources = {entry[0] for entry in known}
            if source not in known_sources:  # mô hình đôi khi viết sai tên nguồn ("Quy quy trình...") -> lấy tên thật gần nhất
                close = difflib.get_close_matches(source, known_sources, n=1, cutoff=0.75)
                if close:
                    source = close[0]
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
