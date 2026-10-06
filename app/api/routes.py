from datetime import datetime, timezone
from functools import lru_cache
import logging
import secrets
from typing import Any, Literal
import uuid

from fastapi import APIRouter, BackgroundTasks, Depends, File, Form, Header, HTTPException, Query, Request, UploadFile
from fastapi.responses import JSONResponse, PlainTextResponse
from pathlib import Path

from app.config import Settings, get_settings
from app.models.schemas import (
    AnalyticsSummary,
    ApiKeyCreateRequest,
    ApiKeyOut,
    AuthResponse,
    ChatRequest,
    ChatResponse,
    FeedbackCreateRequest,
    FeedbackStatusRequest,
    LeaderUpsertRequest,
    ManageNoteCreateRequest,
    ManageNoteUpdateRequest,
    ReportKeyCreateRequest,
    LoginRequest,
    NoteCreateRequest,
    NoteUpdateRequest,
    RefreshRequest,
    SearchResult,
    SignupRequest,
    ThreadMessage,
    ThreadSummary,
)
from app.rag.answer_format import to_plain_text
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
from app.services.rag_service import RAGService

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api")


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


def require_admin(
    settings: Settings = Depends(get_settings),
    x_admin_token: str | None = Header(default=None),
) -> None:
    if not settings.admin_token:
        raise HTTPException(status_code=503, detail="ADMIN_TOKEN chưa được cấu hình trong .env")
    # So sánh trên bytes: compare_digest với str ném TypeError (-> 500) nếu token gửi lên có ký tự ngoài ASCII.
    if not x_admin_token or not secrets.compare_digest(x_admin_token.encode(), settings.admin_token.encode()):
        raise HTTPException(status_code=401, detail="Admin token không hợp lệ")


def require_user(
    authorization: str | None = Header(default=None),
    auth: AuthService = Depends(get_auth_service),
    employees: EmployeeDirectoryService = Depends(get_employee_directory),
    settings: Settings = Depends(get_settings),
) -> dict:
    if not authorization or not authorization.lower().startswith("bearer "):
        raise HTTPException(status_code=401, detail="Cần đăng nhập để dùng trợ lý")
    token = authorization.split(" ", 1)[1]
    try:
        user = auth.get_user(token)
    except AuthError as error:
        raise HTTPException(status_code=401, detail="Phiên đăng nhập đã hết hạn, vui lòng đăng nhập lại") from error
    _require_verified_company_user(user, settings)
    try:
        user["employee_profile"] = employees.get_active_employee(user.get("email"))
    except Exception:
        user["employee_profile"] = None
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


@router.get("/health")
def health(rag: RAGService = Depends(get_rag_service)) -> dict[str, str | int]:
    """Endpoint này bị Docker HEALTHCHECK gọi mỗi 30s + frontend gọi mỗi lần load trang,
    nên chỉ lấy đúng cột 'status' — tránh kéo cả nội dung note (có thể rất nặng với file
    docx nhiều trang) về chỉ để đếm số lượng."""
    notes = rag.supabase.select("knowledge_notes", {"select": "status"})
    approved_count = sum(1 for note in notes if note.get("status") == "approved")
    return {
        "status": "ok",
        "service": "viet-anh-staff-assistant",
        "storage": "supabase",
        "knowledge_notes": len(notes),
        "approved_notes": approved_count,
    }


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
    thread_id: str,
    user: dict = Depends(require_user),
    history: ChatHistoryService = Depends(get_chat_history_service),
) -> list[dict[str, object]]:
    return history.list_messages(thread_id, user["id"])


@router.delete("/chat/threads/{thread_id}")
def delete_thread(
    thread_id: str,
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
        if is_new_thread:
            history.create_thread_with_id(thread_id, user_id, question.strip()[:60])
        elif not history.thread_belongs_to(thread_id, user_id):
            return  # thread_id không thuộc user này (vd đã bị xoá) -> bỏ qua, không ghi nhầm
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


def _track(background_tasks: BackgroundTasks, rag: Any, feature: str, user: dict | None = None, meta: dict | None = None) -> None:
    """Ghi sự kiện sử dụng sau khi đã trả response (không làm chậm người dùng). Thiếu dịch vụ -> bỏ qua."""
    usage = getattr(rag, "usage", None)
    if usage is not None:
        background_tasks.add_task(usage.log, feature, user, meta)


@router.post("/chat-staff", response_model=ChatResponse)
def chat_staff(
    request: ChatRequest,
    background_tasks: BackgroundTasks,
    user: dict = Depends(require_user),
    rag: RAGService = Depends(get_rag_service),
    history: ChatHistoryService = Depends(get_chat_history_service),
) -> ChatResponse:
    is_new_thread = not request.thread_id
    thread_id = request.thread_id or str(uuid.uuid4())
    department, _role = _viewer_department_role(user)
    try:
        result = rag.chat(
            request.question,
            [turn.model_dump() for turn in request.history],
            asker_department=department,
        )
    except Exception as error:
        message = str(error)
        if "API_KEY_INVALID" in message or "API key not valid" in message:
            raise HTTPException(
                status_code=502,
                detail="API key không hợp lệ hoặc đã bị thu hồi. Hãy cập nhật trong .env rồi restart backend.",
            ) from error
        raise HTTPException(status_code=502, detail="LLM hiện không thể xử lý yêu cầu. Kiểm tra log backend.") from error
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
                status_code=502,
                detail="API key không hợp lệ hoặc đã bị thu hồi. Hãy cập nhật trong .env rồi restart backend.",
            ) from error
        raise HTTPException(status_code=502, detail="LLM hiện không thể xử lý yêu cầu. Kiểm tra log backend.") from error
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
    return {"status": "received", "id": str(record.get("id", ""))}


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
            detail="Chưa thực hiện được — kiểm tra đã chạy migration 20261005000000_knowledge_leaders_and_history.sql trên Supabase chưa.",
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
def manage_get_note(note_id: str, user: dict = Depends(require_user), manage: ManageService = Depends(get_manage_service)) -> dict:
    profile = _manage_call(manage.profile, user.get("email"))
    return _manage_call(manage.get_note, profile, note_id)


@router.put("/manage/notes/{note_id}")
def manage_update_note(
    note_id: str,
    body: ManageNoteUpdateRequest,
    background_tasks: BackgroundTasks,
    user: dict = Depends(require_user),
    manage: ManageService = Depends(get_manage_service),
    rag: RAGService = Depends(get_rag_service),
) -> dict:
    profile = _manage_call(manage.profile, user.get("email"))
    note = _manage_call(manage.update_note, profile, note_id, body.model_dump(exclude_none=True))
    _track(background_tasks, rag, "sua_tri_thuc", user, {"phong_ban": note.get("department")})
    return {"id": note["id"]}


@router.delete("/manage/notes/{note_id}")
def manage_delete_note(
    note_id: str,
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
def manage_versions(note_id: str, user: dict = Depends(require_user), manage: ManageService = Depends(get_manage_service)) -> list[dict]:
    profile = _manage_call(manage.profile, user.get("email"))
    return _manage_call(manage.versions, profile, note_id)


@router.post("/manage/notes/{note_id}/restore/{version_id}")
def manage_restore(
    note_id: str,
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
def admin_upsert_leader(body: LeaderUpsertRequest, manage: ManageService = Depends(get_manage_service)) -> dict:
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
) -> dict[str, str]:
    try:
        feedback.set_status(str(feedback_id), body.status)
    except FeedbackNotFoundError as error:
        raise HTTPException(status_code=404, detail=str(error)) from error
    return {"status": body.status}


@router.get("/admin/notes", dependencies=[Depends(require_admin)])
def admin_list_notes(
    status: str = Query("draft"),
    admin: AdminService = Depends(get_admin_service),
) -> list[dict[str, object]]:
    return admin.list_notes(status)


@router.get("/admin/notes/detail", dependencies=[Depends(require_admin)])
def admin_get_note(note_id: str, admin: AdminService = Depends(get_admin_service)) -> dict[str, object]:
    try:
        return admin.get_note(note_id)
    except NoteNotFoundError as error:
        raise HTTPException(status_code=404, detail=str(error)) from error


@router.put("/admin/notes", dependencies=[Depends(require_admin)])
def admin_update_note(
    note_id: str,
    body: NoteUpdateRequest,
    admin: AdminService = Depends(get_admin_service),
) -> dict[str, str]:
    try:
        admin.save_note(note_id, body.model_dump())
    except NoteNotFoundError as error:
        raise HTTPException(status_code=404, detail=str(error)) from error
    return {"status": "saved"}


@router.post("/admin/notes/approve", dependencies=[Depends(require_admin)])
def admin_approve_note(note_id: str, admin: AdminService = Depends(get_admin_service)) -> dict[str, str]:
    try:
        admin.set_status(note_id, "approved")
    except NoteNotFoundError as error:
        raise HTTPException(status_code=404, detail=str(error)) from error
    return {"status": "approved"}


@router.post("/admin/notes/reject", dependencies=[Depends(require_admin)])
def admin_reject_note(note_id: str, admin: AdminService = Depends(get_admin_service)) -> dict[str, str]:
    try:
        admin.set_status(note_id, "rejected")
    except NoteNotFoundError as error:
        raise HTTPException(status_code=404, detail=str(error)) from error
    return {"status": "rejected"}


@router.delete("/admin/notes", dependencies=[Depends(require_admin)])
def admin_delete_note(note_id: str, admin: AdminService = Depends(get_admin_service)) -> dict[str, str]:
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
async def admin_upload_knowledge(
    file: UploadFile = File(...),
    department: str = Form("Unassigned"),
    title: str | None = Form(None),
    rag: RAGService = Depends(get_rag_service),
) -> dict[str, object]:
    content = await file.read()
    try:
        return KnowledgeIngestService(rag.admin, rag.llm, getattr(rag, "semantic_store", getattr(rag, "vector_store", None))).ingest(file.filename or "upload", content, department, title)
    except ValueError as error:
        raise HTTPException(status_code=400, detail=str(error)) from error
    except Exception as error:
        raise HTTPException(status_code=500, detail=f"Không thể ghi dữ liệu vào Supabase: {error}") from error


# ---------------------------------------------------------------------------
# API báo cáo cho Major OS ("Kết nối app với Major OS v2"): chỉ-đọc, key riêng, lọc theo thời gian, phân trang bằng con trỏ.
# ---------------------------------------------------------------------------
REPORT_SPEC_PATH = Path(__file__).parent.parent / "static" / "major_os_report_spec.md"
NO_STORE = {"Cache-Control": "no-store"}


def require_report_key(
    request: Request,
    background_tasks: BackgroundTasks,
    authorization: str | None = Header(default=None),
    keys: ReportKeyService = Depends(get_report_key_service),
) -> dict[str, Any]:
    caller = (request.headers.get("x-forwarded-for", "").split(",")[0].strip()) or (request.client.host if request.client else "?")
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
