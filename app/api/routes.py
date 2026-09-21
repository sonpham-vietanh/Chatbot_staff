from functools import lru_cache
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
from app.services.admin_service import AdminService, NoteNotFoundError
from app.services.api_key_service import ApiKeyNotFoundError, ApiKeyService
from app.services.auth_service import AuthError, AuthService
from app.services.chat_history_service import ChatHistoryService
from app.services.knowledge_ingest import KnowledgeIngestService
from app.services.rag_service import RAGService

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


def require_admin(
    settings: Settings = Depends(get_settings),
    x_admin_token: str | None = Header(default=None),
) -> None:
    if not settings.admin_token:
        raise HTTPException(status_code=503, detail="ADMIN_TOKEN chưa được cấu hình trong .env")
    if not x_admin_token or not secrets.compare_digest(x_admin_token, settings.admin_token):
        raise HTTPException(status_code=401, detail="Admin token không hợp lệ")


def require_user(
    authorization: str | None = Header(default=None),
    auth: AuthService = Depends(get_auth_service),
) -> dict:
    if not authorization or not authorization.lower().startswith("bearer "):
        raise HTTPException(status_code=401, detail="Cần đăng nhập để dùng trợ lý")
    token = authorization.split(" ", 1)[1]
    try:
        return auth.get_user(token)
    except AuthError as error:
        raise HTTPException(status_code=401, detail="Phiên đăng nhập đã hết hạn, vui lòng đăng nhập lại") from error


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


@router.get("/debug/search", response_model=list[SearchResult])
def debug_search(
    q: str = Query(min_length=2),
    user_department: str | None = None,
    department: str | None = None,
    rag: RAGService = Depends(get_rag_service),
) -> list[SearchResult]:
    return rag.search(q, user_department or department)


def _check_email_domain(email: str, settings: Settings) -> None:
    if not settings.allowed_email_domains:
        return
    allowed = [d.strip().casefold().lstrip("@") for d in settings.allowed_email_domains.split(",") if d.strip()]
    if not allowed:
        return
    domain = email.strip().casefold().rsplit("@", 1)[-1]
    if domain not in allowed:
        domains_text = ", ".join(f"@{d}" for d in allowed)
        raise HTTPException(status_code=400, detail=f"Chỉ email công ty ({domains_text}) mới được đăng ký tài khoản.")


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
        raise HTTPException(status_code=400, detail=error.detail) from error
    if not result.get("access_token"):
        raise HTTPException(
            status_code=400,
            detail="Tài khoản đã được tạo nhưng cần xác nhận email. Liên hệ admin để bật đăng nhập ngay không cần xác nhận email.",
        )
    return AuthResponse(access_token=result["access_token"], refresh_token=result["refresh_token"], user=result["user"])


@router.post("/auth/login", response_model=AuthResponse)
def login(body: LoginRequest, auth: AuthService = Depends(get_auth_service)) -> AuthResponse:
    try:
        result = auth.login(body.email, body.password)
    except AuthError as error:
        raise HTTPException(status_code=401, detail="Email hoặc mật khẩu không đúng") from error
    return AuthResponse(access_token=result["access_token"], refresh_token=result["refresh_token"], user=result["user"])


@router.post("/auth/refresh", response_model=AuthResponse)
def refresh_token(body: RefreshRequest, auth: AuthService = Depends(get_auth_service)) -> AuthResponse:
    try:
        result = auth.refresh(body.refresh_token)
    except AuthError as error:
        raise HTTPException(status_code=401, detail="Phiên đăng nhập đã hết hạn, vui lòng đăng nhập lại") from error
    return AuthResponse(access_token=result["access_token"], refresh_token=result["refresh_token"], user=result["user"])


@router.get("/auth/me")
def me(user: dict = Depends(require_user)) -> dict:
    return {
        "id": user["id"],
        "email": user.get("email"),
        "display_name": (user.get("user_metadata") or {}).get("display_name"),
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
    try:
        result = rag.chat(
            request.question,
            request.user_department or request.department,
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
    background_tasks.add_task(
        _persist_chat_turn, history, thread_id, is_new_thread, user["id"], request.question, result["answer"]
    )
    background_tasks.add_task(_log_chat, rag.supabase, request.question, result)
    return ChatResponse(**result, thread_id=thread_id)


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
def admin_revoke_api_key(key_id: str, api_keys: ApiKeyService = Depends(get_api_key_service)) -> dict[str, str]:
    try:
        api_keys.revoke_key(key_id)
    except ApiKeyNotFoundError as error:
        raise HTTPException(status_code=404, detail=str(error)) from error
    return {"status": "revoked"}


@router.delete("/admin/api-keys/{key_id}", dependencies=[Depends(require_admin)])
def admin_delete_api_key(key_id: str, api_keys: ApiKeyService = Depends(get_api_key_service)) -> dict[str, str]:
    api_keys.delete_key(key_id)
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
        return KnowledgeIngestService(rag.admin, rag.llm).ingest(file.filename or "upload", content, department, title)
    except ValueError as error:
        raise HTTPException(status_code=400, detail=str(error)) from error
    except Exception as error:
        raise HTTPException(status_code=500, detail=f"Không thể ghi dữ liệu vào Supabase: {error}") from error
