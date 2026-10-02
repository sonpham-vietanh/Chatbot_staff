from functools import lru_cache
import logging
import secrets
from typing import Any
import uuid

from fastapi import APIRouter, BackgroundTasks, Depends, File, Form, Header, HTTPException, Query, UploadFile

from app.config import Settings, get_settings
from app.models.schemas import (
    AnalyticsSummary,
    ApiKeyCreateRequest,
    ApiKeyOut,
    AuthResponse,
    ChatRequest,
    ChatResponse,
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
    return ChatResponse(**result)


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
        return KnowledgeIngestService(rag.admin, rag.llm, rag.vector_store).ingest(file.filename or "upload", content, department, title)
    except ValueError as error:
        raise HTTPException(status_code=400, detail=str(error)) from error
    except Exception as error:
        raise HTTPException(status_code=500, detail=f"Không thể ghi dữ liệu vào Supabase: {error}") from error
