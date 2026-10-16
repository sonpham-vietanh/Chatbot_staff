from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from functools import lru_cache
import hashlib
import logging
import re
import secrets
import threading
import time
from typing import Any, Literal, get_args
import uuid

from fastapi import APIRouter, BackgroundTasks, Depends, File, Form, Header, HTTPException, Query, Request, UploadFile
from fastapi.responses import JSONResponse, PlainTextResponse
from pathlib import Path

from app.config import Settings, get_settings
from app.models.schemas import MAX_HISTORY_ITEMS, MAX_TURN_CHARS
from app.models.schemas import (
    AnalyticsSummary,
    ApiKeyCreateRequest,
    ApiKeyOut,
    AuthResponse,
    ChangeRequestCreate,
    ChangeRequestReviewRequest,
    ChatRequest,
    ChatResponse,
    Department,
    DirectoryCreateRequest,
    DirectoryDeactivateRequest,
    DirectoryFields,
    DirectoryImportRequest,
    FeedbackCreateRequest,
    FeedbackStatusRequest,
    LeaderUpsertRequest,
    ManageNoteCreateRequest,
    ManageNoteUpdateRequest,
    ProfileUpdateRequest,
    ReportKeyCreateRequest,
    WigCreateRequest,
    WigProgressRequest,
    WigUpdateRequest,
    LoginRequest,
    NoteCreateRequest,
    NoteUpdateRequest,
    RefreshRequest,
    SearchResult,
    SignupRequest,
    ThreadMessage,
    ThreadSummary,
    UUIDStr,
)
from app.rag.answer_format import to_plain_text
from app.rag.lexical_store import fold
from app.rag.suggestions import suggest_followups
from app.services.admin_service import AdminService, NoteNotFoundError
from app.services.api_key_service import ApiKeyNotFoundError, ApiKeyService, normalize_origin
from app.services.auth_service import AuthError, AuthService, is_public_auth_key
from app.services.chat_history_service import ChatHistoryService
from app.services.feedback_service import FeedbackNotFoundError, FeedbackService
from app.services.manage_service import ManageError, ManageService
from app.services.report_service import RATE_LIMIT_PER_MINUTE, ReportKeyService, ReportService
from app.services.usage_service import FEATURES, ReportQueryError, UsageService, clamp_limit, parse_time, to_vn
from app.services.employee_directory_service import EmployeeDirectoryService
from app.services.knowledge_ingest import KnowledgeIngestService
from app.services.llm import LLMError
from app.services.directory_service import EMPLOYMENT_TYPES, DirectoryError
from app.services.profile_service import ProfileError, leave_entitlement, tenure_text
from app.services.wig_service import WigError
from app.services.rag_service import RAGService

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api")
DEPARTMENTS = get_args(Department)


@lru_cache
def get_rag_service() -> RAGService:
    """Singleton dùng chung cho mọi request (1 SupabaseClient, 1 embedding/LLM provider)."""
    return RAGService(get_settings())


def get_admin_service(rag: RAGService = Depends(get_rag_service)) -> AdminService:
    return rag.admin


def get_auth_service(rag: RAGService = Depends(get_rag_service)) -> AuthService:
    return rag.auth


def get_chat_history_service(rag: RAGService = Depends(get_rag_service)) -> ChatHistoryService:
    return rag.chat_history


def get_api_key_service(rag: RAGService = Depends(get_rag_service)) -> ApiKeyService:
    return rag.api_keys


def get_feedback_service(rag: RAGService = Depends(get_rag_service)) -> FeedbackService:
    return rag.feedback


def get_manage_service(rag: RAGService = Depends(get_rag_service)) -> ManageService:
    return rag.manage


def get_usage_service(rag: RAGService = Depends(get_rag_service)) -> UsageService:
    return rag.usage


def get_report_service(rag: RAGService = Depends(get_rag_service)) -> ReportService:
    return rag.report


def get_report_key_service(rag: RAGService = Depends(get_rag_service)) -> ReportKeyService:
    return rag.report_keys


def get_employee_directory(rag: RAGService = Depends(get_rag_service)) -> EmployeeDirectoryService:
    return EmployeeDirectoryService(rag.supabase)


def require_widget_key(
    background_tasks: BackgroundTasks,
    x_widget_key: str | None = Header(default=None),
    origin: str | None = Header(default=None),
    api_keys: ApiKeyService = Depends(get_api_key_service),
) -> dict[str, Any]:
    if not x_widget_key:
        raise HTTPException(status_code=401, detail="Thiếu X-Widget-Key")
    try:
        record = api_keys.get_active_key(x_widget_key)
    except Exception as error:
        # Database tạm lỗi: trả JSON 503 có "detail" như mọi lỗi khác, không để thành 500 dạng text thô.
        logger.warning("Không tra được widget key", exc_info=True)
        raise HTTPException(status_code=503, detail="Dịch vụ tạm thời không khả dụng, vui lòng thử lại sau.") from error
    if not record:
        raise HTTPException(status_code=401, detail="Widget key không hợp lệ hoặc đã bị thu hồi")
    allowed_origin = normalize_origin(record["allowed_origin"])
    if origin and (allowed_origin is None or normalize_origin(origin) != allowed_origin):
        raise HTTPException(status_code=403, detail="Origin không được phép dùng widget key này")
    # Ghi last_used_at sau khi đã trả response — không để 1 lần ghi theo dõi làm chậm câu trả lời.
    background_tasks.add_task(api_keys.touch_last_used, record["id"])
    return record


ADMIN_FAIL_LIMIT = 10
ADMIN_FAIL_WINDOW_SECONDS = 300.0
_ADMIN_FAILS: dict[str, list[float]] = {}
_ADMIN_FAILS_LOCK = threading.Lock()
"""Chặn dò ADMIN_TOKEN: quá ADMIN_FAIL_LIMIT lần sai trong ADMIN_FAIL_WINDOW_SECONDS từ cùng một IP thì trả 429."""


def _admin_fail_wait(caller: str, *, record: bool) -> float:
    now = time.monotonic()
    with _ADMIN_FAILS_LOCK:
        if len(_ADMIN_FAILS) > 2000:  # không để bảng phình vô hạn khi bị quét từ nhiều IP
            for key in [k for k, hits in _ADMIN_FAILS.items() if not hits or now - hits[-1] > ADMIN_FAIL_WINDOW_SECONDS]:
                del _ADMIN_FAILS[key]
        hits = [t for t in _ADMIN_FAILS.get(caller, []) if now - t < ADMIN_FAIL_WINDOW_SECONDS]
        if record:
            hits.append(now)
        _ADMIN_FAILS[caller] = hits
        return (ADMIN_FAIL_WINDOW_SECONDS - (now - hits[0])) if len(hits) >= ADMIN_FAIL_LIMIT else 0.0


def require_admin(
    request: Request,
    settings: Settings = Depends(get_settings),
    x_admin_token: str | None = Header(default=None),
) -> None:
    if not settings.admin_token:
        raise HTTPException(status_code=503, detail="ADMIN_TOKEN chưa được cấu hình trong .env")
    caller = _client_ip(request)
    wait = _admin_fail_wait(caller, record=False)
    if wait:
        raise HTTPException(status_code=429, detail="Nhập sai quá nhiều lần, thử lại sau ít phút.", headers={"Retry-After": str(int(wait) + 1)})
    # So sánh trên bytes: compare_digest với str ném TypeError (-> 500) nếu token gửi lên có ký tự ngoài ASCII.
    if not x_admin_token or not secrets.compare_digest(x_admin_token.encode(), settings.admin_token.encode()):
        if x_admin_token:  # thiếu hẳn header không phải là dò token
            _admin_fail_wait(caller, record=True)
        raise HTTPException(status_code=401, detail="Admin token không hợp lệ")


USER_CACHE_SECONDS = 60.0
USER_CACHE_MAX_ENTRIES = 500
"""Trước đây MỌI request có xác thực (kể cả mỗi tin nhắn chat) phải xác thực lại qua
2 lượt gọi Supabase tuần tự (auth.get_user + tra hồ sơ nhân viên) — cache ngắn hạn theo
token để các request liên tiếp trong cùng phiên không phải xác thực lại mỗi lần. Đánh đổi
chấp nhận được: tối đa 60s trễ nếu tài khoản bị khoá/thu hồi quyền giữa chừng. Cache có giới hạn số mục
(xoá mục hết hạn rồi mục cũ nhất) và chỉ lưu kết quả thành công; mọi thay đổi danh bạ nhân sự gọi clear_user_cache()."""
_USER_CACHE: dict[str, tuple[dict, float]] = {}
_USER_CACHE_LOCK = threading.Lock()


def clear_user_cache() -> None:
    with _USER_CACHE_LOCK:
        _USER_CACHE.clear()


def _cache_user(cache_key: str, user: dict) -> None:
    now = time.monotonic()
    with _USER_CACHE_LOCK:
        if len(_USER_CACHE) >= USER_CACHE_MAX_ENTRIES:
            for key in [k for k, (_, at) in _USER_CACHE.items() if now - at >= USER_CACHE_SECONDS]:
                del _USER_CACHE[key]
            while len(_USER_CACHE) >= USER_CACHE_MAX_ENTRIES:
                del _USER_CACHE[min(_USER_CACHE, key=lambda k: _USER_CACHE[k][1])]
        _USER_CACHE[cache_key] = (user, now)


def require_user(
    authorization: str | None = Header(default=None),
    auth: AuthService = Depends(get_auth_service),
    employees: EmployeeDirectoryService = Depends(get_employee_directory),
    settings: Settings = Depends(get_settings),
) -> dict:
    if not authorization or not authorization.lower().startswith("bearer "):
        raise HTTPException(status_code=401, detail="Cần đăng nhập để dùng trợ lý")
    token = authorization.split(" ", 1)[1].strip()
    if not token or not token.isascii():  # token thật luôn là ASCII; ký tự lạ làm httpx ném lỗi -> 500
        raise HTTPException(status_code=401, detail="Phiên đăng nhập không hợp lệ, vui lòng đăng nhập lại")
    cache_key = hashlib.sha256(token.encode()).hexdigest()
    cached = _USER_CACHE.get(cache_key)
    if cached is not None and time.monotonic() - cached[1] < USER_CACHE_SECONDS:
        return cached[0]
    try:
        user = auth.get_user(token)
    except AuthError as error:
        if error.status_code >= 500:  # dịch vụ xác thực tạm lỗi: KHÔNG báo "hết hạn" làm người dùng bị đăng xuất oan
            raise HTTPException(status_code=503, detail="Dịch vụ đăng nhập tạm thời không phản hồi, vui lòng thử lại sau ít phút.") from error
        raise HTTPException(status_code=401, detail="Phiên đăng nhập đã hết hạn, vui lòng đăng nhập lại") from error
    _require_verified_company_user(user, settings)
    try:
        user["employee_profile"] = employees.get_active_employee(user.get("email"))
    except Exception:
        user["employee_profile"] = None
    _cache_user(cache_key, user)
    return user


def _is_company_email(email: str | None, settings: Settings) -> bool:
    allowed = [d.strip().casefold().lstrip("@") for d in (settings.allowed_email_domains or "").split(",") if d.strip()]
    if not allowed:
        raise HTTPException(status_code=503, detail="ALLOWED_EMAIL_DOMAINS chưa được cấu hình trong .env")
    if not email or "@" not in email:
        return False
    domain = email.strip().casefold().rsplit("@", 1)[-1]
    return domain in allowed


def _require_verified_company_user(user: dict, settings: Settings) -> None:
    if not user.get("email_confirmed_at"):
        raise HTTPException(status_code=403, detail="Cần xác minh email trước khi sử dụng tài khoản.")
    if not _is_company_email(user.get("email"), settings):
        raise HTTPException(status_code=403, detail="Chỉ email đã xác minh thuộc domain công ty mới được sử dụng chatbot.")


def _viewer_department_role(user: dict) -> tuple[str | None, str]:
    """Dùng department HR cấp; user_metadata do người dùng chỉnh sửa nên không tin cậy."""
    profile = user.get("employee_profile") or {}
    app_metadata = user.get("app_metadata") or {}
    return profile.get("department"), app_metadata.get("role") or "staff"


HEALTH_CACHE_SECONDS = 30.0
_HEALTH_CACHE: dict[str, Any] = {"value": None, "at": 0.0}


@router.get("/health")
def health(rag: RAGService = Depends(get_rag_service)) -> dict[str, str | int]:
    """Endpoint này bị Docker HEALTHCHECK gọi mỗi 30s + frontend gọi mỗi lần load trang,
    nên chỉ lấy đúng cột 'status' — tránh kéo cả nội dung note (có thể rất nặng với file
    docx nhiều trang) về chỉ để đếm số lượng."""
    cached = _HEALTH_CACHE.get("value")
    if cached is not None and time.monotonic() - _HEALTH_CACHE["at"] < HEALTH_CACHE_SECONDS:
        return cached
    notes = rag.supabase.select("knowledge_notes", {"select": "status"})  # lỗi DB thì ném ra như cũ: healthcheck phải báo hỏng
    approved_count = sum(1 for note in notes if note.get("status") == "approved")
    value = {
        "status": "ok",
        "service": "viet-anh-staff-assistant",
        "storage": "supabase",
        "knowledge_notes": len(notes),
        "approved_notes": approved_count,
    }
    _HEALTH_CACHE.update(value=value, at=time.monotonic())
    return value


@router.get("/auth/config")
def auth_config(settings: Settings = Depends(get_settings)) -> dict[str, str | bool | None]:
    """Public Supabase Auth config only; the anon key is designed for browser use."""
    domains_configured = any(d.strip() for d in (settings.allowed_email_domains or "").split(","))
    enabled = bool(settings.supabase_url and is_public_auth_key(settings.supabase_anon_key) and domains_configured)
    return {
        "enabled": enabled,
        "supabase_url": settings.supabase_url if enabled else None,
        "supabase_anon_key": settings.supabase_anon_key if enabled else None,
    }


@router.get("/debug/search", response_model=list[SearchResult], dependencies=[Depends(require_admin)])
def debug_search(
    q: str = Query(min_length=2),
    user_department: str | None = None,
    department: str | None = None,
    rag: RAGService = Depends(get_rag_service),
) -> list[SearchResult]:
    return rag.search(q, user_department or department)


def _check_email_domain(email: str, settings: Settings) -> None:
    if not _is_company_email(email, settings):
        raise HTTPException(status_code=400, detail="Chỉ email thuộc domain công ty mới được đăng ký tài khoản.")


@router.post("/auth/signup", response_model=AuthResponse)
def signup(
    body: SignupRequest,
    auth: AuthService = Depends(get_auth_service),
    settings: Settings = Depends(get_settings),
) -> AuthResponse:
    _check_email_domain(body.email, settings)
    try:
        result = auth.signup(body.email, body.password, body.display_name)
    except AuthError as error:
        if error.status_code >= 500:
            raise HTTPException(status_code=error.status_code, detail=error.detail) from error
        raise HTTPException(status_code=400, detail=error.detail) from error
    if not result.get("access_token"):
        raise HTTPException(
            status_code=400,
            detail="Tài khoản đã được tạo nhưng cần xác nhận email. Liên hệ admin để bật đăng nhập ngay không cần xác nhận email.",
        )
    _require_verified_company_user(auth.get_user(result["access_token"]), settings)
    return AuthResponse(access_token=result["access_token"], refresh_token=result["refresh_token"], user=result["user"])


@router.post("/auth/login", response_model=AuthResponse)
def login(
    body: LoginRequest,
    auth: AuthService = Depends(get_auth_service),
    settings: Settings = Depends(get_settings),
) -> AuthResponse:
    try:
        _check_email_domain(body.email, settings)
        result = auth.login(body.email, body.password)
        _require_verified_company_user(auth.get_user(result["access_token"]), settings)
    except AuthError as error:
        if error.status_code >= 500:
            raise HTTPException(status_code=error.status_code, detail=error.detail) from error
        raise HTTPException(status_code=401, detail="Email hoặc mật khẩu không đúng") from error
    return AuthResponse(access_token=result["access_token"], refresh_token=result["refresh_token"], user=result["user"])


@router.post("/auth/refresh", response_model=AuthResponse)
def refresh_token(
    body: RefreshRequest,
    auth: AuthService = Depends(get_auth_service),
    settings: Settings = Depends(get_settings),
) -> AuthResponse:
    try:
        result = auth.refresh(body.refresh_token)
        _require_verified_company_user(auth.get_user(result["access_token"]), settings)
    except AuthError as error:
        if error.status_code >= 500:
            raise HTTPException(status_code=error.status_code, detail=error.detail) from error
        raise HTTPException(status_code=401, detail="Phiên đăng nhập đã hết hạn, vui lòng đăng nhập lại") from error
    return AuthResponse(access_token=result["access_token"], refresh_token=result["refresh_token"], user=result["user"])


@router.get("/auth/me")
def me(user: dict = Depends(require_user)) -> dict:
    profile = user.get("employee_profile")
    metadata = user.get("user_metadata") or {}
    return {
        "id": user["id"],
        "email": user.get("email"),
        "display_name": (profile or {}).get("display_name") or metadata.get("display_name") or metadata.get("full_name") or metadata.get("name"),
        "employee": profile,
    }


@router.get("/chat/threads", response_model=list[ThreadSummary])
def list_threads(
    user: dict = Depends(require_user),
    history: ChatHistoryService = Depends(get_chat_history_service),
) -> list[dict[str, object]]:
    return history.list_threads(user["id"])


@router.get("/chat/threads/{thread_id}/messages", response_model=list[ThreadMessage])
def thread_messages(
    thread_id: UUIDStr,
    user: dict = Depends(require_user),
    history: ChatHistoryService = Depends(get_chat_history_service),
) -> list[dict[str, object]]:
    return history.list_messages(thread_id, user["id"])


@router.delete("/chat/threads/{thread_id}")
def delete_thread(
    thread_id: UUIDStr,
    user: dict = Depends(require_user),
    history: ChatHistoryService = Depends(get_chat_history_service),
) -> dict[str, str]:
    history.delete_thread(thread_id, user["id"])
    return {"status": "deleted"}


def _persist_chat_turn(
    history: ChatHistoryService,
    thread_id: str,
    is_new_thread: bool,
    user_id: str,
    question: str,
    answer: str,
) -> None:
    """Chạy sau khi response đã trả về cho user (BackgroundTasks) — lưu lịch sử không
    được làm chậm câu trả lời, và là best-effort giống chat_logs từ trước tới giờ."""
    try:
        if is_new_thread or not history.thread_belongs_to(thread_id, user_id):
            # Mã do client tự tạo ở lượt đầu cũng được nhận. Mã đã thuộc người khác thì tạo thất bại (trùng khoá) -> nhảy xuống
            # except, không ghi gì: không bao giờ ghi nhầm vào cuộc trò chuyện của người khác.
            history.create_thread_with_id(thread_id, user_id, question.strip()[:60])
        history.add_turn(thread_id, question, answer)
    except Exception:
        pass


def _log_chat(supabase, question: str, result: dict[str, Any]) -> None:
    try:
        supabase.insert("chat_logs", [{
            "question": question,
            "answer": result["answer"],
            "grounded": result["grounded"],
            "citations": result["citations"],
        }], returning=False)
    except Exception:
        pass  # log chat là best-effort, không được làm hỏng câu trả lời cho user


def _llm_failure(error: Exception) -> HTTPException:
    """Đổi lỗi gọi dịch vụ AI thành thông báo đúng nguyên nhân (khoá sai, hết hạn mức, quá tải, máy tắt); chi tiết kỹ thuật
    chỉ nằm trong log, không trả cho người dùng (có thể chứa địa chỉ endpoint)."""
    logger.warning("Gọi LLM lỗi: %s", error)
    # KHÔNG dùng 502/504: Cloudflare thay mọi phản hồi 502/504 của máy chủ gốc bằng trang lỗi HTML riêng của nó,
    # làm mất thông báo; 503 thì đi qua nguyên vẹn.
    if isinstance(error, LLMError):
        headers = {"Retry-After": str(int(error.retry_after or 5))} if error.status == 429 else None
        code = error.status or "kết nối"
        return HTTPException(status_code=429 if error.status == 429 else 503, detail=f"{error.user_message} (mã: {code})", headers=headers)
    return HTTPException(status_code=503, detail=f"Trợ lý chưa xử lý được yêu cầu (lỗi: {type(error).__name__}). Kiểm tra log backend.")


def _track(background_tasks: BackgroundTasks, rag: Any, feature: str, user: dict | None = None, meta: dict | None = None) -> None:
    """Ghi sự kiện sử dụng sau khi đã trả response (không làm chậm người dùng). Thiếu dịch vụ -> bỏ qua."""
    usage = getattr(rag, "usage", None)
    if usage is not None:
        background_tasks.add_task(usage.log, feature, user, meta)


WIG_WORD = re.compile(r"\bwigs?\b", re.IGNORECASE)
OTHER_PERSON_WIG = re.compile(r"\bwigs?\s+(?:của|cua)\s+(?!tôi|toi|mình|minh|em\b|tui)", re.IGNORECASE)
WIG_CONCEPT = re.compile(r"\b(là gì|nghĩa là|la gi|định nghĩa|dinh nghia)\b", re.IGNORECASE)
TEAM_WORD = re.compile(r"\b(?:team|nhom|cap duoi|nhan vien cua toi|nhan vien cua minh|nguoi bao cao)\b")
WIG_DONE = re.compile(r"\b(?:da hoan thanh|hoan thanh\b.{0,15}\b(?:nao|chua)|da xong|da dat)\b")
WIG_DROPPED = re.compile(r"\b(?:huy|da bo|bi bo|bo qua)\b")
WIG_REMIND = re.compile(r"\b(?:nhac|nhac nho)\b")


def _own_wig_answer(rag: Any, user: dict, question: str) -> dict | None:
    """"WIG của tôi tới đâu rồi?": trả lời thẳng từ cơ sở dữ liệu WIG của CHÍNH người hỏi (không qua mô hình, không lộ WIG người khác).
    Phân biệt WIG đang chạy / đã xong / đã bỏ / của nhóm (người báo cáo trực tiếp) / nhờ nhắc nhở. Hỏi khái niệm WIG hoặc WIG của
    một người cụ thể thì vẫn đi đường hỏi đáp thường."""
    wigs = getattr(rag, "wigs", None)
    if wigs is None or not WIG_WORD.search(question):
        return None
    folded = fold(question)
    team = bool(TEAM_WORD.search(folded))
    if not team and (OTHER_PERSON_WIG.search(question) or WIG_CONCEPT.search(question)):
        return None
    email = user.get("email") or ""
    try:
        if team:
            text = wigs.chat_team(email)
        elif WIG_REMIND.search(folded):
            text = ("Mình chưa có tính năng nhắc nhở tự động. Bạn cập nhật tiến độ ở trang **Hồ sơ**; khi cần cứ hỏi "
                    "\"WIG của tôi tới đâu rồi\" mình sẽ báo lại ngay.")
        elif WIG_DROPPED.search(folded):
            text = wigs.chat_view(email, "dropped")
        elif WIG_DONE.search(folded):
            text = wigs.chat_view(email, "done")
        else:
            text = wigs.chat_summary(email, user.get("display_name"))
        return {"answer": text, "grounded": True, "citations": []}
    except Exception:  # noqa: BLE001 — chưa chạy migration hoặc DB lỗi: để đường hỏi đáp thường trả lời
        logger.warning("Không đọc được WIG cho chatbot", exc_info=True)
        return None


OWN_PROFILE_Q = re.compile(
    r"\b(?:ho so|thong tin|chuc danh|chuc vu|phong ban|bo phan|quan ly truc tiep|tham nien|ngay vao lam|ngay vao cong ty)\b.{0,25}\b(?:cua toi|cua minh|cua em|cua tui)\b"
    r"|\b(?:toi|minh) (?:dang )?(?:lam|thuoc) (?:o )?(?:phong|bo phan) nao\b|\bai la quan ly (?:truc tiep )?cua (?:toi|minh)\b")
OWN_POINTS_Q = re.compile(r"\bdiem\b.{0,25}\b(?:cua toi|cua minh|cua tui)\b|\b(?:toi|minh) (?:co|duoc) bao nhieu diem\b")
OWN_TEAM_Q = re.compile(
    r"\bcap duoi\b.{0,15}\b(?:toi|minh)\b|\b(?:toi|minh)\b.{0,12}\bquan ly\b.{0,12}\b(?:nhung ai|ai|bao nhieu nguoi)\b"
    r"|\bai\b.{0,15}\bbao cao cho (?:toi|minh)\b|\bnhan vien cua (?:toi|minh)\b|\bteam cua (?:toi|minh)\b.{0,15}\bgom\b")
SELF_PRONOUN = re.compile(r"\b(?:toi|tui|minh|em)\b")
OTHER_PERSON = re.compile(r"\b(?:cua|chi|anh|ong|thay)\s+(?!toi\b|tui\b|minh\b|em\b)[a-z]{2,}")
POLICY_WORDS = re.compile(r"\b(?:phep|luong|thuong|bao hiem|bhxh|hop dong|thu viec|nghi|hoc bong|quy dinh|duoc bao nhieu)\b")
PRIVATE_Q = re.compile(r"\b(?:so dien thoai|sdt|sinh nhat|ngay sinh|dia chi|zalo|email)\b")
FIELD_RULES = (
    ("job_title", re.compile(r"\b(?:chuc danh|chuc vu|vi tri cong viec)\b")),
    ("department", re.compile(r"\b(?:phong ban|bo phan|thuoc phong|lam phong|phong nao)\b")),
    ("manager", re.compile(r"\b(?:sep|quan ly truc tiep|ai quan ly)\b")),
    ("start", re.compile(r"\b(?:vao lam|vao cong ty|vao truong|ngay vao|bat dau lam)\b")),
    ("tenure", re.compile(r"\b(?:tham nien|lam duoc bao lau|lam bao lau)\b")),
    ("campus", re.compile(r"\bco so\b")),
    ("teaching", re.compile(r"\b(?:day mon|mon day|day lop|to chuyen mon|to nao)\b")),
    ("employment", re.compile(r"\b(?:hinh thuc|co huu|thinh giang)\b")),
)


def _profile_rows(profile: dict, keys: list[str]) -> list[tuple[str, Any]]:
    manager = (profile.get("manager") or {}).get("display_name") or (profile.get("manager") or {}).get("email")
    table = {
        "job_title": [("Chức danh", profile.get("job_title"))],
        "department": [("Phòng ban", profile.get("department"))],
        "manager": [("Quản lý trực tiếp", manager)],
        "start": [("Ngày vào công ty", profile.get("employment_start_date")), ("Thâm niên", profile.get("tenure"))],
        "tenure": [("Thâm niên", profile.get("tenure"))],
        "campus": [("Cơ sở", profile.get("campus"))],
        "teaching": [("Tổ chuyên môn", profile.get("team_name")), ("Môn dạy", profile.get("subjects"))],
        "employment": [("Hình thức", profile.get("employment_type_label"))],
    }
    return [row for key in keys for row in table[key]]


def _own_info_answer(rag: Any, user: dict, question: str) -> dict | None:
    """"Hồ sơ / điểm / chức danh / sếp / ngày vào làm / cấp dưới của tôi": trả lời từ dữ liệu của CHÍNH người hỏi, không qua mô hình.
    Chỉ nêu thông tin công việc do HR quản lý (không có ngày sinh, địa chỉ, số điện thoại... — câu hỏi và câu trả lời của chat được lưu
    lại nên không đưa thông tin riêng tư vào). Câu hỏi chính sách có kèm hoàn cảnh cá nhân ("tôi vào làm 6 tháng thì được mấy ngày phép")
    đi đường hỏi đáp thường, được bổ sung khối "NGƯỜI HỎI"."""
    folded = fold(question)
    email = user.get("email") or ""
    short = len(folded.split()) <= 10
    is_self = bool(SELF_PRONOUN.search(folded)) and not OTHER_PERSON.search(folded) and short and not POLICY_WORDS.search(folded)
    try:
        if OWN_POINTS_Q.search(folded) and getattr(rag, "points", None) is not None:
            summary = rag.points.summary(email, recent=3)
            lines = [f"Bạn đang có **{summary['total']} điểm** đóng góp."]
            lines += [f"- {item['label']}: +{item['points']}" for item in summary["recent"]]
            lines += ["", "Xem đầy đủ ở trang **Hồ sơ**."]
            return {"answer": "\n".join(lines), "grounded": True, "citations": []}
        if OWN_TEAM_Q.search(folded) and getattr(rag, "wigs", None) is not None:
            reports = rag.wigs.reports_of(email)
            if not reports:
                return {"answer": "Mình chưa thấy ai báo cáo trực tiếp cho bạn trong danh bạ nhân sự. Nếu thiếu, bạn nhờ HR cập nhật quản lý trực tiếp.",
                        "grounded": True, "citations": []}
            lines = [f"Bạn đang quản lý trực tiếp **{len(reports)} người**:", ""]
            lines += [f"- {person.get('display_name') or person['email']} — {person.get('job_title') or 'chưa có chức danh'}" for person in reports[:20]]
            return {"answer": "\n".join(lines), "grounded": True, "citations": []}
        if is_self and PRIVATE_Q.search(folded):
            if "email" in folded:
                return {"answer": f"Email đăng nhập của bạn là **{email}**.", "grounded": True, "citations": []}
            return {"answer": "Đây là thông tin riêng tư nên mình không hiển thị trong chat. Bạn xem và sửa ở trang **Hồ sơ**.",
                    "grounded": True, "citations": []}
        keys = [key for key, pattern in FIELD_RULES if pattern.search(folded)] if is_self else []
        wants_all = bool(OWN_PROFILE_Q.search(folded))
        if (keys or wants_all) and getattr(rag, "profiles", None) is not None:
            profile = rag.profiles.get(email)
            if not profile.get("in_directory"):
                return {"answer": "Mình chưa thấy bạn trong danh bạ nhân sự nên chưa có thông tin công việc để báo. Bạn liên hệ HR để được thêm vào nhé.",
                        "grounded": True, "citations": []}
            if keys:
                rows = [(label, value) for label, value in _profile_rows(profile, keys) if value]
                if not rows:
                    return {"answer": "Danh bạ nhân sự chưa có thông tin này của bạn. Bạn gửi yêu cầu bổ sung ở trang **Hồ sơ** nhé.",
                            "grounded": True, "citations": []}
                if len(rows) == 1:
                    return {"answer": f"{rows[0][0]} của bạn: **{rows[0][1]}**.", "grounded": True, "citations": []}
                return {"answer": "\n".join(f"- {label}: **{value}**" for label, value in rows), "grounded": True, "citations": []}
            rows = _profile_rows(profile, ["job_title", "department", "campus", "manager", "start", "employment"])
            lines = [f"- {label}: **{value}**" for label, value in [("Họ tên", profile.get("display_name")), *rows] if value]
            return {"answer": "Thông tin công việc của bạn trong danh bạ:\n" + "\n".join(lines) + "\n\nSai chỗ nào thì gửi yêu cầu chỉnh sửa ở trang **Hồ sơ**.",
                    "grounded": True, "citations": []}
    except Exception:  # noqa: BLE001 — chưa chạy migration hoặc DB lỗi: để đường hỏi đáp thường trả lời
        logger.warning("Không đọc được hồ sơ/điểm cho chatbot", exc_info=True)
    return None


ASKER_CACHE_SECONDS = 60.0
ASKER_CACHE_MAX = 500
_ASKER_CACHE: dict[str, tuple[str | None, float]] = {}
_ASKER_CACHE_LOCK = threading.Lock()


def _build_asker(rag: Any, user: dict) -> str | None:
    email = (user.get("email") or "").strip().casefold()
    directory = getattr(rag, "directory", None)
    wigs = getattr(rag, "wigs", None)
    if not email or directory is None:
        return None
    row = directory.get(email)
    if not row or not row.get("active"):
        return f"- Email đăng nhập: {email}\n- Chưa có trong danh bạ nhân sự nên chưa biết ngày vào làm, phòng ban, chức danh (khuyên liên hệ HR để được thêm vào)."
    manager = reports = my_wigs = None
    with ThreadPoolExecutor(3) as pool:
        f_manager = pool.submit(directory.get, row["manager_email"]) if row.get("manager_email") else None
        f_reports = pool.submit(wigs.reports_of, email) if wigs is not None else None
        f_wigs = pool.submit(wigs.list_for, email) if wigs is not None else None
        manager = f_manager.result() if f_manager else None
        reports = f_reports.result() if f_reports else []
        my_wigs = f_wigs.result() if f_wigs else []
    lines = [f"- Họ tên: {row.get('display_name')}", f"- Email: {email}", f"- Phòng ban: {row.get('department')}", f"- Chức danh: {row.get('job_title')}"]
    for label, key in (("Cơ sở", "campus"), ("Tổ chuyên môn", "team_name"), ("Môn dạy", "subjects"), ("Kiêm nhiệm", "extra_roles")):
        if row.get(key):
            lines.append(f"- {label}: {row[key]}")
    kind = row.get("employment_type")
    if kind:
        lines.append(f"- Hình thức: {EMPLOYMENT_TYPES.get(kind, kind)}")
    if manager:
        lines.append(f"- Quản lý trực tiếp: {manager.get('display_name') or manager['email']}")
    start = row.get("employment_start_date")
    if start:
        lines.append(f"- Ngày vào công ty: {start} (thâm niên {tenure_text(start)})")
        if kind in (None, "", "co_huu"):
            entitlement = leave_entitlement(start)
            if entitlement:
                lines.append(f"- Phép năm theo thâm niên (tính từ ngày vào công ty; chưa trừ thời gian thỉnh giảng / nghỉ tạm thời nếu có; chưa biết số ngày đã nghỉ): {entitlement}")
        else:
            lines.append("- Quy định phép năm theo thâm niên áp dụng cho nhân sự cơ hữu; người này không phải cơ hữu nên cần hỏi HR.")
    if reports:
        lines.append(f"- Cấp dưới trực tiếp ({len(reports)}): " + "; ".join(f"{p.get('display_name') or p['email']} ({p.get('job_title') or '?'})" for p in reports[:10]))
    active = [w for w in (my_wigs or []) if w["status"] == "active"]
    if active:
        lines.append("- WIG đang chạy: " + "; ".join(f"{w['title']} ({w['period_label']}) {w['progress']}%" + (f", hạn {w['due_date']}" if w.get("due_date") else "") for w in active[:5]))
    return "\n".join(lines)


def _asker_context(rag: Any, user: dict) -> str | None:
    """Thông tin công việc của người đang hỏi (lấy từ danh bạ) đưa vào câu lệnh gửi mô hình để bot trả lời "của tôi" và tính phép theo thâm niên.
    Không có ngày sinh, địa chỉ, số điện thoại. Cache ngắn theo email để không thêm vài lượt truy vấn cho mỗi tin nhắn; lỗi thì bỏ qua."""
    email = (user.get("email") or "").strip().casefold()
    now = time.monotonic()
    cached = _ASKER_CACHE.get(email)
    if cached is not None and now - cached[1] < ASKER_CACHE_SECONDS:
        return cached[0]
    try:
        text = _build_asker(rag, user)
    except Exception:  # noqa: BLE001 — chưa chạy migration hoặc DB lỗi: trả lời không cá nhân hoá
        logger.warning("Không dựng được thông tin người hỏi", exc_info=True)
        return None
    with _ASKER_CACHE_LOCK:
        if len(_ASKER_CACHE) >= ASKER_CACHE_MAX:
            _ASKER_CACHE.clear()
        _ASKER_CACHE[email] = (text, now)
    return text


def _stored_history(history: ChatHistoryService, thread_id: str, user_id: str) -> list[dict]:
    """Client mở lại một cuộc trò chuyện cũ mà không gửi lịch sử: lấy các tin gần nhất đã lưu ở server."""
    try:
        rows = history.list_messages(thread_id, user_id)
    except Exception:  # noqa: BLE001
        return []
    return [{"role": row["role"], "content": str(row["content"])[:MAX_TURN_CHARS]} for row in rows[-MAX_HISTORY_ITEMS:] if row.get("role") in ("user", "assistant")]


@router.post("/chat-staff", response_model=ChatResponse)
def chat_staff(
    request: ChatRequest,
    background_tasks: BackgroundTasks,
    user: dict = Depends(require_user),
    rag: RAGService = Depends(get_rag_service),
    history: ChatHistoryService = Depends(get_chat_history_service),
) -> ChatResponse:
    is_new_thread = not request.thread_id
    thread_id = (request.thread_id or str(uuid.uuid4())).lower()
    department, _role = _viewer_department_role(user)
    result = _own_wig_answer(rag, user, request.question) or _own_info_answer(rag, user, request.question)
    if result is None:
        turns = [turn.model_dump() for turn in request.history]
        if not turns and request.thread_id:
            turns = _stored_history(history, thread_id, user["id"])
        extra = {}
        asker = _asker_context(rag, user)
        if asker:
            extra["asker"] = asker
        try:
            result = rag.chat(request.question, turns, asker_department=department, **extra)
        except Exception as error:
            message = str(error)
            if "API_KEY_INVALID" in message or "API key not valid" in message:
                raise HTTPException(
                    status_code=503,
                    detail="API key không hợp lệ hoặc đã bị thu hồi. Hãy cập nhật trong .env rồi restart backend.",
                ) from error
            raise _llm_failure(error) from error
    result = {**result, "suggestions": suggest_followups(request.question, result["answer"])}
    background_tasks.add_task(
        _persist_chat_turn, history, thread_id, is_new_thread, user["id"], request.question, result["answer"]
    )
    background_tasks.add_task(_log_chat, rag.supabase, request.question, result)
    _track(background_tasks, rag, "hoi_dap", user, {"tra_loi_duoc": bool(result["grounded"]), "so_nguon": len(result["citations"])})
    return ChatResponse(**result, thread_id=thread_id)


@router.post("/widget/chat", response_model=ChatResponse)
def widget_chat(
    request: ChatRequest,
    background_tasks: BackgroundTasks,
    _widget: dict[str, Any] = Depends(require_widget_key),
    rag: RAGService = Depends(get_rag_service),
) -> ChatResponse:
    """Server-to-server chat for an approved partner UI; it does not require a second login."""
    try:
        result = rag.chat(
            request.question,
            [turn.model_dump() for turn in request.history],
        )
    except Exception as error:
        message = str(error)
        if "API_KEY_INVALID" in message or "API key not valid" in message:
            raise HTTPException(
                status_code=503,
                detail="API key không hợp lệ hoặc đã bị thu hồi. Hãy cập nhật trong .env rồi restart backend.",
            ) from error
        raise _llm_failure(error) from error
    # Bên tích hợp hiển thị nguyên văn câu trả lời nên phải là văn bản thuần — giao diện
    # của mình (chat-staff) mới tự render **in đậm**, giao diện đối tác thì không.
    result["answer"] = to_plain_text(result["answer"])
    background_tasks.add_task(_log_chat, rag.supabase, request.question, result)
    _track(background_tasks, rag, "hoi_dap_nhung", None,
           {"tra_loi_duoc": bool(result["grounded"]), "so_nguon": len(result["citations"]), "nguon": _widget.get("label")})
    return ChatResponse(**result)


@router.post("/feedback")
def report_wrong_answer(
    body: FeedbackCreateRequest,
    background_tasks: BackgroundTasks,
    user: dict = Depends(require_user),
    feedback: FeedbackService = Depends(get_feedback_service),
    rag: RAGService = Depends(get_rag_service),
) -> dict[str, str]:
    """Nút "Báo sai" trên giao diện chat. Lưu nguyên văn câu hỏi/câu trả lời/nguồn để admin
    kiểm tra được kể cả khi cuộc trò chuyện đã bị xoá."""
    payload = body.model_dump(mode="json")
    try:
        record = feedback.create(user, payload)
    except Exception as error:
        logger.warning("Không lưu được báo cáo sai", exc_info=True)
        raise HTTPException(status_code=503, detail="Chưa gửi được báo cáo, vui lòng thử lại sau.") from error
    _track(background_tasks, rag, "bao_sai", user, {"ly_do": payload.get("reason")})
    points = getattr(rag, "points", None)
    if points is not None:
        background_tasks.add_task(points.award, user.get("email"), "bao_sai_gui", "feedback", str(record.get("id", "")))
    return {"status": "received", "id": str(record.get("id", ""))}


# ---------------------------------------------------------------------------
# Hồ sơ, WIG và điểm đóng góp của chính người đăng nhập
# ---------------------------------------------------------------------------
def get_profile_service(rag: RAGService = Depends(get_rag_service)):
    return rag.profiles


def get_wig_service(rag: RAGService = Depends(get_rag_service)):
    return rag.wigs


def get_points_service(rag: RAGService = Depends(get_rag_service)):
    return rag.points


def _wig_call(action, *args, **kwargs):
    try:
        return action(*args, **kwargs)
    except (WigError, ProfileError) as error:
        raise HTTPException(status_code=error.status_code, detail=error.detail) from error
    except HTTPException:
        raise
    except Exception as error:
        logger.warning("Lỗi hồ sơ/WIG", exc_info=True)
        raise HTTPException(
            status_code=503,
            detail=_infra_detail(error, "Chưa dùng được hồ sơ và WIG — kiểm tra đã chạy các migration 20261007000000_profiles_wigs_points.sql và 20261009000000_profile_more_fields.sql trên Supabase chưa."),
        ) from error


@router.get("/me/profile")
def my_profile(user: dict = Depends(require_user), profiles=Depends(get_profile_service), points=Depends(get_points_service)) -> dict:
    email = user.get("email") or ""
    profile = _wig_call(profiles.get, email)
    if not profile.get("display_name"):
        metadata = user.get("user_metadata") or {}
        profile["display_name"] = metadata.get("display_name") or metadata.get("full_name") or metadata.get("name")
    profile["points"] = _wig_call(points.summary, email)
    return profile


@router.put("/me/profile")
def update_my_profile(body: ProfileUpdateRequest, user: dict = Depends(require_user), profiles=Depends(get_profile_service)) -> dict[str, str]:
    if not _wig_call(profiles.has_consent, user.get("email") or ""):
        raise HTTPException(status_code=403, detail="Bạn cần xác nhận đồng ý về mục đích sử dụng thông tin trước khi lưu hồ sơ.")
    _wig_call(profiles.update_self, user.get("email") or "", body.model_dump(mode="json", exclude_unset=True))
    return {"status": "saved"}


@router.post("/me/profile/consent")
def consent_profile(user: dict = Depends(require_user), profiles=Depends(get_profile_service)) -> dict[str, str]:
    return {"status": "saved", "consent_at": _wig_call(profiles.give_consent, user.get("email") or "")}


# ---------------------------------------------------------------------------
# Danh bạ nhân sự (HR) và yêu cầu chỉnh sửa hồ sơ
# ---------------------------------------------------------------------------
DIRECTORY_MIGRATION_HINT = "Chưa dùng được danh bạ nhân sự — kiểm tra đã chạy migration 20261010000000_directory_admin_and_change_requests.sql trên Supabase chưa."


def get_directory_service(rag: RAGService = Depends(get_rag_service)):
    return rag.directory


def get_change_request_service(rag: RAGService = Depends(get_rag_service)):
    return rag.change_requests


def _is_schema_error(error: Exception) -> bool:
    """Lỗi do thiếu bảng/cột (chưa chạy migration) khác lỗi tạm thời của Supabase: chỉ nhắc chạy migration khi đúng nguyên nhân đó."""
    text = str(error)
    response = getattr(error, "response", None)
    if response is not None:
        text += " " + (getattr(response, "text", "") or "")
    return any(code in text for code in ("PGRST205", "PGRST204", "42P01", "42703", "does not exist", "schema cache"))


def _infra_detail(error: Exception, migration_hint: str) -> str:
    return migration_hint if _is_schema_error(error) else "Chưa thực hiện được lúc này, vui lòng thử lại sau ít phút. Nếu lỗi lặp lại, báo quản trị viên."


def _directory_call(action, *args, _error_status: int = 503, **kwargs):
    """Lỗi nghiệp vụ -> đúng mã trong DirectoryError. Lỗi hạ tầng (thường do chưa chạy migration) -> `_error_status`
    (503 cho nhân viên; route admin dùng 500 vì trang admin hiểu 503 là "chưa cấu hình ADMIN_TOKEN" và tự đăng xuất)."""
    try:
        return action(*args, **kwargs)
    except DirectoryError as error:
        raise HTTPException(status_code=error.status_code, detail=error.detail) from error
    except HTTPException:
        raise
    except Exception as error:
        logger.warning("Lỗi danh bạ/yêu cầu chỉnh sửa", exc_info=True)
        raise HTTPException(status_code=_error_status, detail=_infra_detail(error, DIRECTORY_MIGRATION_HINT)) from error


def _admin_directory_call(action, *args, **kwargs):
    result = _directory_call(action, *args, _error_status=500, **kwargs)
    clear_user_cache()  # danh bạ vừa đổi: phòng ban/chức danh trong cache đăng nhập (tối đa 60s) không còn đúng
    return result


@router.get("/me/change-requests")
def my_change_requests(user: dict = Depends(require_user), requests=Depends(get_change_request_service)) -> dict:
    return {"items": _directory_call(requests.list_for, user.get("email") or "")}


@router.post("/me/change-requests")
def create_change_request(body: ChangeRequestCreate, user: dict = Depends(require_user), requests=Depends(get_change_request_service)) -> dict:
    return _directory_call(requests.create, user.get("email") or "", body.field, body.new_value, body.reason)


@router.post("/me/change-requests/{request_id}/cancel")
def cancel_change_request(request_id: uuid.UUID, user: dict = Depends(require_user), requests=Depends(get_change_request_service)) -> dict:
    return _directory_call(requests.cancel, user.get("email") or "", str(request_id))


@router.get("/admin/directory", dependencies=[Depends(require_admin)])
def admin_directory_list(
    q: str = Query("", max_length=200),
    active: Literal["true", "false", "all"] = Query("true"),
    limit: int = Query(50, ge=1, le=200),
    offset: int = Query(0, ge=0),
    directory=Depends(get_directory_service),
) -> dict:
    return _admin_directory_call(directory.list, q, active, limit, offset)


@router.post("/admin/directory", dependencies=[Depends(require_admin)])
def admin_directory_create(body: DirectoryCreateRequest, directory=Depends(get_directory_service)) -> dict:
    return _admin_directory_call(directory.create, body.model_dump())


# Đường dẫn cố định (import) phải khai báo trước đường dẫn có tham số {email}.
@router.post("/admin/directory/import", dependencies=[Depends(require_admin)])
def admin_directory_import(body: DirectoryImportRequest, directory=Depends(get_directory_service)) -> dict:
    return _admin_directory_call(directory.import_rows, body.rows, body.dry_run)


@router.put("/admin/directory/{email}", dependencies=[Depends(require_admin)])
def admin_directory_update(email: str, body: DirectoryFields, directory=Depends(get_directory_service)) -> dict:
    return _admin_directory_call(directory.update, email, body.model_dump(exclude_unset=True))


@router.post("/admin/directory/{email}/deactivate", dependencies=[Depends(require_admin)])
def admin_directory_deactivate(email: str, body: DirectoryDeactivateRequest | None = None, directory=Depends(get_directory_service)) -> dict:
    return _admin_directory_call(directory.deactivate, email, body.end_date if body else None)


@router.post("/admin/directory/{email}/reactivate", dependencies=[Depends(require_admin)])
def admin_directory_reactivate(email: str, directory=Depends(get_directory_service)) -> dict:
    return _admin_directory_call(directory.reactivate, email)


@router.get("/admin/directory/{email}/history", dependencies=[Depends(require_admin)])
def admin_directory_history(email: str, directory=Depends(get_directory_service)) -> dict:
    return {"items": _admin_directory_call(directory.history, email)}


@router.get("/admin/change-requests", dependencies=[Depends(require_admin)])
def admin_change_requests(
    status: Literal["pending", "approved", "rejected", "cancelled", "all"] = Query("pending"),
    requests=Depends(get_change_request_service),
) -> dict:
    return _admin_directory_call(requests.admin_list, status)


@router.post("/admin/change-requests/{request_id}/approve", dependencies=[Depends(require_admin)])
def admin_approve_change_request(request_id: uuid.UUID, body: ChangeRequestReviewRequest | None = None, requests=Depends(get_change_request_service)) -> dict:
    return _admin_directory_call(requests.approve, str(request_id), body.note if body else None)


@router.post("/admin/change-requests/{request_id}/reject", dependencies=[Depends(require_admin)])
def admin_reject_change_request(request_id: uuid.UUID, body: ChangeRequestReviewRequest, requests=Depends(get_change_request_service)) -> dict:
    return _admin_directory_call(requests.reject, str(request_id), body.note)


@router.get("/me/wigs")
def my_wigs(include_closed: bool = Query(False), user: dict = Depends(require_user), wigs=Depends(get_wig_service)) -> dict:
    return {"items": _wig_call(wigs.list_for, user.get("email") or "", include_closed)}


@router.post("/me/wigs")
def create_my_wig(body: WigCreateRequest, user: dict = Depends(require_user), wigs=Depends(get_wig_service)) -> dict:
    return _wig_call(wigs.create, user.get("email") or "", body.model_dump(mode="json"))


@router.put("/me/wigs/{wig_id}")
def update_my_wig(wig_id: uuid.UUID, body: WigUpdateRequest, user: dict = Depends(require_user), wigs=Depends(get_wig_service)) -> dict:
    return _wig_call(wigs.update, user.get("email") or "", str(wig_id), body.model_dump(mode="json", exclude_unset=True))


@router.post("/me/wigs/{wig_id}/progress")
def add_wig_progress(wig_id: uuid.UUID, body: WigProgressRequest, user: dict = Depends(require_user), wigs=Depends(get_wig_service)) -> dict:
    return _wig_call(wigs.add_progress, user.get("email") or "", str(wig_id), body.value, body.note)


@router.get("/wigs/{wig_id}/updates")
def wig_history(wig_id: uuid.UUID, user: dict = Depends(require_user), wigs=Depends(get_wig_service)) -> dict:
    """Lịch sử cập nhật: chính chủ, quản lý trực tiếp và admin."""
    return {"items": _wig_call(wigs.updates, user.get("email") or "", str(wig_id))}


@router.get("/team/wigs")
def team_wigs(user: dict = Depends(require_user), wigs=Depends(get_wig_service)) -> dict:
    """WIG đang chạy của những người báo cáo trực tiếp cho người đăng nhập (theo bảng HR)."""
    return {"items": _wig_call(wigs.team, user.get("email") or "")}


def _manage_call(action, *args, **kwargs):
    """Chuyển lỗi nghiệp vụ/DB của trang quản lý thành mã HTTP rõ ràng cho giao diện."""
    try:
        return action(*args, **kwargs)
    except ManageError as error:
        raise HTTPException(status_code=error.status_code, detail=error.detail) from error
    except NoteNotFoundError as error:
        raise HTTPException(status_code=404, detail=str(error)) from error
    except HTTPException:
        raise
    except Exception as error:
        logger.warning("Lỗi trang quản lý tri thức", exc_info=True)
        raise HTTPException(
            status_code=500,
            detail=_infra_detail(error, "Chưa thực hiện được — kiểm tra đã chạy migration 20261005000000_knowledge_leaders_and_history.sql trên Supabase chưa."),
        ) from error


@router.get("/manage/me")
def manage_me(user: dict = Depends(require_user), manage: ManageService = Depends(get_manage_service)) -> dict:
    return _manage_call(manage.profile, user.get("email"))


@router.get("/manage/notes")
def manage_list_notes(
    q: str = Query("", max_length=100),
    department: str = Query("", max_length=30),
    user: dict = Depends(require_user),
    manage: ManageService = Depends(get_manage_service),
) -> list[dict]:
    profile = _manage_call(manage.profile, user.get("email"))
    return _manage_call(manage.list_notes, profile, q, department)


@router.post("/manage/notes")
def manage_create_note(
    body: ManageNoteCreateRequest,
    background_tasks: BackgroundTasks,
    user: dict = Depends(require_user),
    manage: ManageService = Depends(get_manage_service),
    rag: RAGService = Depends(get_rag_service),
) -> dict:
    profile = _manage_call(manage.profile, user.get("email"))
    note = _manage_call(manage.create_note, profile, body.title, body.department, body.content, body.status, body.access_level)
    _track(background_tasks, rag, "tao_tri_thuc", user, {"phong_ban": note.get("department")})
    return {"id": note["id"]}


@router.get("/manage/notes/{note_id}")
def manage_get_note(note_id: UUIDStr, user: dict = Depends(require_user), manage: ManageService = Depends(get_manage_service)) -> dict:
    profile = _manage_call(manage.profile, user.get("email"))
    return _manage_call(manage.get_note, profile, note_id)


@router.put("/manage/notes/{note_id}")
def manage_update_note(
    note_id: UUIDStr,
    body: ManageNoteUpdateRequest,
    background_tasks: BackgroundTasks,
    user: dict = Depends(require_user),
    manage: ManageService = Depends(get_manage_service),
    rag: RAGService = Depends(get_rag_service),
) -> dict:
    if not body.base_updated_at:
        raise HTTPException(status_code=422, detail="Thiếu thông tin phiên bản của tài liệu. Tải lại trang rồi sửa lại để tránh ghi đè bản của người khác.")
    profile = _manage_call(manage.profile, user.get("email"))
    note = _manage_call(manage.update_note, profile, note_id, body.model_dump(exclude_none=True))
    _track(background_tasks, rag, "sua_tri_thuc", user, {"phong_ban": note.get("department")})
    return {"id": note["id"]}


@router.delete("/manage/notes/{note_id}")
def manage_delete_note(
    note_id: UUIDStr,
    background_tasks: BackgroundTasks,
    user: dict = Depends(require_user),
    manage: ManageService = Depends(get_manage_service),
    rag: RAGService = Depends(get_rag_service),
) -> dict:
    profile = _manage_call(manage.profile, user.get("email"))
    _manage_call(manage.delete_note, profile, note_id)
    _track(background_tasks, rag, "xoa_tri_thuc", user)
    return {"status": "deleted"}


@router.get("/manage/notes/{note_id}/versions")
def manage_versions(note_id: UUIDStr, user: dict = Depends(require_user), manage: ManageService = Depends(get_manage_service)) -> list[dict]:
    profile = _manage_call(manage.profile, user.get("email"))
    return _manage_call(manage.versions, profile, note_id)


@router.post("/manage/notes/{note_id}/restore/{version_id}")
def manage_restore(
    note_id: UUIDStr,
    version_id: uuid.UUID,
    background_tasks: BackgroundTasks,
    user: dict = Depends(require_user),
    manage: ManageService = Depends(get_manage_service),
    rag: RAGService = Depends(get_rag_service),
) -> dict:
    profile = _manage_call(manage.profile, user.get("email"))
    note = _manage_call(manage.restore, profile, note_id, str(version_id))
    _track(background_tasks, rag, "khoi_phuc_tri_thuc", user, {"phong_ban": note.get("department")})
    return {"id": note["id"]}


@router.get("/admin/leaders", dependencies=[Depends(require_admin)])
def admin_list_leaders(manage: ManageService = Depends(get_manage_service)) -> list[dict]:
    return _manage_call(manage.list_leaders)


@router.put("/admin/leaders", dependencies=[Depends(require_admin)])
def admin_upsert_leader(body: LeaderUpsertRequest, manage: ManageService = Depends(get_manage_service), settings: Settings = Depends(get_settings)) -> dict:
    if not _is_company_email(body.email, settings):
        raise HTTPException(status_code=422, detail="Email leader phải thuộc miền công ty.")
    return _manage_call(manage.upsert_leader, body.email, body.display_name, body.departments, body.active)


@router.delete("/admin/leaders", dependencies=[Depends(require_admin)])
def admin_delete_leader(email: str = Query(min_length=3, max_length=200), manage: ManageService = Depends(get_manage_service)) -> dict:
    _manage_call(manage.delete_leader, email)
    return {"status": "deleted"}


@router.get("/admin/feedback", dependencies=[Depends(require_admin)])
def admin_list_feedback(
    status: Literal["open", "resolved", "dismissed", "all"] = Query("open"),
    feedback: FeedbackService = Depends(get_feedback_service),
) -> dict[str, object]:
    try:
        return {"items": feedback.list(status), "open_count": feedback.count_open()}
    except Exception as error:
        logger.warning("Không đọc được answer_feedback", exc_info=True)
        raise HTTPException(
            # 500 chứ không 503: trang admin hiểu 503 là "chưa cấu hình ADMIN_TOKEN" và tự đăng xuất.
            status_code=500,
            detail="Chưa đọc được báo cáo — kiểm tra đã chạy migration 20261004000000_answer_feedback.sql trên Supabase chưa.",
        ) from error


@router.post("/admin/feedback/{feedback_id}/status", dependencies=[Depends(require_admin)])
def admin_set_feedback_status(
    feedback_id: uuid.UUID,
    body: FeedbackStatusRequest,
    feedback: FeedbackService = Depends(get_feedback_service),
    rag: RAGService = Depends(get_rag_service),
) -> dict[str, str]:
    try:
        row = feedback.set_status(str(feedback_id), body.status)
    except FeedbackNotFoundError as error:
        raise HTTPException(status_code=404, detail=str(error)) from error
    points = getattr(rag, "points", None)
    if body.status == "resolved" and points is not None:  # báo sai được xác nhận và đã sửa: cộng điểm cho người báo (mỗi báo cáo một lần)
        points.award((row or {}).get("user_email"), "bao_sai_xac_nhan", "feedback", str(feedback_id))
    return {"status": body.status}


@router.get("/admin/notes", dependencies=[Depends(require_admin)])
def admin_list_notes(
    status: str = Query("draft"),
    admin: AdminService = Depends(get_admin_service),
) -> list[dict[str, object]]:
    return admin.list_notes(status)


@router.get("/admin/notes/detail", dependencies=[Depends(require_admin)])
def admin_get_note(note_id: UUIDStr, admin: AdminService = Depends(get_admin_service)) -> dict[str, object]:
    try:
        return admin.get_note(note_id)
    except NoteNotFoundError as error:
        raise HTTPException(status_code=404, detail=str(error)) from error


@router.put("/admin/notes", dependencies=[Depends(require_admin)])
def admin_update_note(
    note_id: UUIDStr,
    body: NoteUpdateRequest,
    admin: AdminService = Depends(get_admin_service),
) -> dict[str, str]:
    try:
        admin.save_note(note_id, body.model_dump())
    except NoteNotFoundError as error:
        raise HTTPException(status_code=404, detail=str(error)) from error
    return {"status": "saved"}


@router.post("/admin/notes/approve", dependencies=[Depends(require_admin)])
def admin_approve_note(note_id: UUIDStr, admin: AdminService = Depends(get_admin_service)) -> dict[str, str]:
    try:
        admin.set_status(note_id, "approved")
    except NoteNotFoundError as error:
        raise HTTPException(status_code=404, detail=str(error)) from error
    return {"status": "approved"}


@router.post("/admin/notes/reject", dependencies=[Depends(require_admin)])
def admin_reject_note(note_id: UUIDStr, admin: AdminService = Depends(get_admin_service)) -> dict[str, str]:
    try:
        admin.set_status(note_id, "rejected")
    except NoteNotFoundError as error:
        raise HTTPException(status_code=404, detail=str(error)) from error
    return {"status": "rejected"}


@router.delete("/admin/notes", dependencies=[Depends(require_admin)])
def admin_delete_note(note_id: UUIDStr, admin: AdminService = Depends(get_admin_service)) -> dict[str, str]:
    try:
        admin.get_note(note_id)
    except NoteNotFoundError as error:
        raise HTTPException(status_code=404, detail=str(error)) from error
    admin.delete_note(note_id)
    return {"status": "deleted"}


@router.get("/admin/analytics", response_model=AnalyticsSummary, dependencies=[Depends(require_admin)])
def admin_analytics(rag: RAGService = Depends(get_rag_service)) -> dict[str, object]:
    return rag.analytics.summary()


@router.get("/admin/api-keys", response_model=list[ApiKeyOut], dependencies=[Depends(require_admin)])
def admin_list_api_keys(api_keys: ApiKeyService = Depends(get_api_key_service)) -> list[dict[str, object]]:
    return api_keys.list_keys()


@router.post("/admin/api-keys", response_model=ApiKeyOut, dependencies=[Depends(require_admin)])
def admin_create_api_key(
    body: ApiKeyCreateRequest, api_keys: ApiKeyService = Depends(get_api_key_service)
) -> dict[str, object]:
    return api_keys.create_key(body.label, body.allowed_origin)


@router.post("/admin/api-keys/{key_id}/revoke", dependencies=[Depends(require_admin)])
def admin_revoke_api_key(key_id: uuid.UUID, api_keys: ApiKeyService = Depends(get_api_key_service)) -> dict[str, str]:
    # key_id kiểu UUID: id sai định dạng bị trả 422 ở đây thay vì thành lỗi 400 của PostgREST -> 500.
    try:
        api_keys.revoke_key(str(key_id))
    except ApiKeyNotFoundError as error:
        raise HTTPException(status_code=404, detail=str(error)) from error
    return {"status": "revoked"}


@router.delete("/admin/api-keys/{key_id}", dependencies=[Depends(require_admin)])
def admin_delete_api_key(key_id: uuid.UUID, api_keys: ApiKeyService = Depends(get_api_key_service)) -> dict[str, str]:
    api_keys.delete_key(str(key_id))
    return {"status": "deleted"}


@router.post("/admin/notes/create", dependencies=[Depends(require_admin)])
def admin_create_note(body: NoteCreateRequest, admin: AdminService = Depends(get_admin_service)) -> dict[str, object]:
    note = admin.create_note(body.title, body.department, body.content, body.access_level, body.status)
    return {"status": "created", "id": note["id"]}


@router.post("/admin/upload", dependencies=[Depends(require_admin)])
def admin_upload_knowledge(
    file: UploadFile = File(...),
    department: str = Form("Unassigned"),
    title: str | None = Form(None),
    rag: RAGService = Depends(get_rag_service),
) -> dict[str, object]:
    # Hàm thường (không async): đọc + phân tích + nhúng tệp mất vài giây, chạy ở luồng riêng để không chặn các request khác.
    if department not in DEPARTMENTS:
        raise HTTPException(status_code=422, detail=f"Phòng ban không hợp lệ (chọn một trong: {', '.join(DEPARTMENTS)}).")
    limit = KnowledgeIngestService.max_bytes()
    content = file.file.read(limit + 1)  # đọc tối đa limit+1 byte để biết vượt giới hạn mà không nạp cả tệp khổng lồ vào RAM
    if len(content) > limit:
        raise HTTPException(status_code=413, detail="File vượt quá giới hạn 10 MB")
    try:
        return KnowledgeIngestService(rag.admin, rag.llm, getattr(rag, "semantic_store", getattr(rag, "vector_store", None))).ingest(file.filename or "upload", content, department, (title or "")[:200] or None)
    except ValueError as error:
        raise HTTPException(status_code=400, detail=str(error)) from error
    except Exception as error:
        logger.warning("Upload tri thức lỗi", exc_info=True)
        raise HTTPException(status_code=503, detail="Chưa lưu được tệp vào kho tri thức, vui lòng thử lại sau.") from error


# ---------------------------------------------------------------------------
# API báo cáo cho Major OS ("Kết nối app với Major OS v2"): chỉ-đọc, key riêng, lọc theo thời gian, phân trang bằng con trỏ.
# ---------------------------------------------------------------------------
REPORT_SPEC_PATH = Path(__file__).parent.parent / "static" / "major_os_report_spec.md"
NO_STORE = {"Cache-Control": "no-store"}


def _client_ip(request: Request) -> str:
    """IP người gọi để giới hạn dò key. Không tin phần tử ĐẦU của X-Forwarded-For (client tự đặt được, dùng để vừa né giới hạn
    vừa khoá oan IP của Major OS): ưu tiên CF-Connecting-IP do Cloudflare đặt, không có thì lấy phần tử CUỐI do proxy gần nhất thêm vào."""
    cloudflare = request.headers.get("cf-connecting-ip", "").strip()
    if cloudflare:
        return cloudflare[:64]
    forwarded = [part.strip() for part in request.headers.get("x-forwarded-for", "").split(",") if part.strip()]
    if forwarded:
        return forwarded[-1][:64]
    return request.client.host if request.client else "?"


def require_report_key(
    request: Request,
    background_tasks: BackgroundTasks,
    authorization: str | None = Header(default=None),
    keys: ReportKeyService = Depends(get_report_key_service),
) -> dict[str, Any]:
    caller = _client_ip(request)
    locked = keys.failures.blocked(caller)
    if locked:  # IP này vừa gọi sai key quá nhiều lần: chặn trước khi chạm DB
        raise HTTPException(status_code=429, detail="Gọi sai key quá nhiều lần, thử lại sau.", headers={"Retry-After": str(int(locked) + 1)})
    if not authorization or not authorization.lower().startswith("bearer ") or not authorization[7:].strip():
        keys.failures.check(caller)
        raise HTTPException(status_code=401, detail="Thiếu header Authorization: Bearer <key>", headers={"WWW-Authenticate": "Bearer"})
    try:
        record = keys.authenticate(authorization[7:].strip())
    except Exception as error:
        logger.warning("Không tra được report key", exc_info=True)
        raise HTTPException(status_code=503, detail="Dịch vụ tạm thời không khả dụng, vui lòng thử lại sau.") from error
    if not record:
        keys.failures.check(caller)
        raise HTTPException(status_code=401, detail="Key không hợp lệ hoặc đã bị thu hồi", headers={"WWW-Authenticate": "Bearer"})
    wait = keys.limiter.check(record["id"])
    if wait:
        raise HTTPException(status_code=429, detail=f"Gọi quá nhiều (tối đa {RATE_LIMIT_PER_MINUTE} lần/phút).",
                            headers={"Retry-After": str(int(wait) + 1)})
    background_tasks.add_task(keys.touch, record)
    return record


def _server_time() -> str:
    return to_vn(datetime.now(timezone.utc).isoformat())


def _report_range(tu: str | None, den: str | None):
    try:
        tu_dt, den_dt = parse_time(tu, "tu"), parse_time(den, "den")
    except ReportQueryError as error:
        raise HTTPException(status_code=422, detail=str(error)) from error
    if tu_dt and den_dt and tu_dt > den_dt:
        raise HTTPException(status_code=422, detail="Tham số tu không được muộn hơn den.")
    return tu_dt, den_dt


def _report_call(action, *args, **kwargs):
    try:
        return action(*args, **kwargs)
    except ReportQueryError as error:
        raise HTTPException(status_code=422, detail=str(error)) from error
    except HTTPException:
        raise
    except Exception as error:
        logger.warning("Lỗi API báo cáo", exc_info=True)
        raise HTTPException(status_code=503, detail="Chưa lấy được báo cáo — kiểm tra đã chạy migration 20261006000000_usage_events_and_report_keys.sql chưa.") from error


@router.get("/report/kiem-tra")
def report_ping(request: Request, key: dict = Depends(require_report_key)) -> JSONResponse:
    """Dành cho nút "Thử kết nối" của Major OS: chỉ xác nhận key hợp lệ, không trả dữ liệu người dùng."""
    return JSONResponse({"ok": True, "ten_app": "Trợ lý nội bộ Trường Việt Anh", "phien_ban": request.app.version,
                         "gio_may_chu": _server_time(), "nhan_key": key["label"]}, headers=NO_STORE)


@router.get("/report/tinh-nang")
def report_features(_key: dict = Depends(require_report_key)) -> JSONResponse:
    return JSONResponse({"du_lieu": [{"khoa": k, **v} for k, v in FEATURES.items()]}, headers=NO_STORE)


@router.get("/report/su-kien")
def report_events(
    tu: str | None = Query(None, max_length=40),
    den: str | None = Query(None, max_length=40),
    con_tro: str | None = Query(None, max_length=300),
    gioi_han: int | None = Query(None, ge=1, le=500),
    tinh_nang: str | None = Query(None, max_length=60),
    _key: dict = Depends(require_report_key),
    usage: UsageService = Depends(get_usage_service),
) -> JSONResponse:
    tu_dt, den_dt = _report_range(tu, den)
    rows, next_cursor = _report_call(usage.list_events, tu=tu_dt, den=den_dt, cursor=con_tro, limit=clamp_limit(gioi_han), feature=tinh_nang)
    return JSONResponse({"du_lieu": rows, "trang_sau": next_cursor, "gio_may_chu": _server_time()}, headers=NO_STORE)


@router.get("/report/gop-y")
def report_feedback(
    tu: str | None = Query(None, max_length=40),
    den: str | None = Query(None, max_length=40),
    con_tro: str | None = Query(None, max_length=300),
    gioi_han: int | None = Query(None, ge=1, le=500),
    _key: dict = Depends(require_report_key),
    reports: ReportService = Depends(get_report_service),
) -> JSONResponse:
    tu_dt, den_dt = _report_range(tu, den)
    rows, next_cursor = _report_call(reports.feedback_page, tu=tu_dt, den=den_dt, cursor=con_tro, limit=clamp_limit(gioi_han))
    return JSONResponse({"du_lieu": rows, "trang_sau": next_cursor, "gio_may_chu": _server_time()}, headers=NO_STORE)


@router.get("/report/canh-bao")
def report_alerts(_key: dict = Depends(require_report_key), reports: ReportService = Depends(get_report_service)) -> JSONResponse:
    return JSONResponse({"du_lieu": _report_call(reports.alerts), "gio_may_chu": _server_time()}, headers=NO_STORE)


@router.get("/report/tong-quan")
def report_summary(
    tu: str | None = Query(None, max_length=40),
    den: str | None = Query(None, max_length=40),
    _key: dict = Depends(require_report_key),
    usage: UsageService = Depends(get_usage_service),
) -> JSONResponse:
    tu_dt, den_dt = _report_range(tu, den)
    return JSONResponse({**_report_call(usage.summary, tu=tu_dt, den=den_dt), "gio_may_chu": _server_time()}, headers=NO_STORE)


# ---- quản trị key + file mô tả (admin token) ----
@router.get("/admin/report-keys", dependencies=[Depends(require_admin)])
def admin_list_report_keys(keys: ReportKeyService = Depends(get_report_key_service)) -> list[dict]:
    return _report_call(keys.list)


@router.post("/admin/report-keys", dependencies=[Depends(require_admin)])
def admin_create_report_key(body: ReportKeyCreateRequest, keys: ReportKeyService = Depends(get_report_key_service)) -> JSONResponse:
    """Key gốc chỉ hiện trong response này một lần duy nhất."""
    return JSONResponse(_report_call(keys.create, body.label), headers=NO_STORE)


@router.post("/admin/report-keys/{key_id}/revoke", dependencies=[Depends(require_admin)])
def admin_revoke_report_key(key_id: uuid.UUID, keys: ReportKeyService = Depends(get_report_key_service)) -> dict[str, str]:
    if not _report_call(keys.revoke, str(key_id)):
        raise HTTPException(status_code=404, detail="Không tìm thấy key đang hoạt động.")
    return {"status": "revoked"}


@router.delete("/admin/report-keys/{key_id}", dependencies=[Depends(require_admin)])
def admin_delete_report_key(key_id: uuid.UUID, keys: ReportKeyService = Depends(get_report_key_service)) -> dict[str, str]:
    _report_call(keys.delete, str(key_id))
    return {"status": "deleted"}


def _public_base_url(request: Request, settings: Settings) -> str:
    if settings.public_base_url:
        return settings.public_base_url.rstrip("/")
    host = request.headers.get("x-forwarded-host") or request.headers.get("host") or "localhost"
    local = host.startswith(("localhost", "127.0.0.1"))
    return f"{'http' if local else 'https'}://{host}"


@router.get("/admin/report-spec", dependencies=[Depends(require_admin)])
def admin_report_spec(request: Request, settings: Settings = Depends(get_settings)) -> PlainTextResponse:
    """File mô tả API (mục 5 của tài liệu "Kết nối app với Major OS v2") với địa chỉ thật của máy chủ này."""
    features = "\n".join(f"| `{key}` | {info['ten']} | {info['mo_ta']} |" for key, info in FEATURES.items())
    text = (REPORT_SPEC_PATH.read_text(encoding="utf-8")
            .replace("{{BASE_URL}}", _public_base_url(request, settings))
            .replace("{{FEATURES_TABLE}}", features)
            .replace("{{RATE_LIMIT}}", str(RATE_LIMIT_PER_MINUTE)))
    return PlainTextResponse(text, media_type="text/markdown; charset=utf-8",
                             headers={"Content-Disposition": 'attachment; filename="mo-ta-api-bao-cao-tro-ly-noi-bo.md"'})
