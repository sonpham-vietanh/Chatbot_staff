from functools import lru_cache
import secrets

from fastapi import APIRouter, Depends, File, Form, Header, HTTPException, Query, UploadFile

from app.config import Settings, get_settings
from app.models.schemas import ChatRequest, ChatResponse, NoteCreateRequest, NoteUpdateRequest, SearchResult
from app.services.admin_service import AdminService, NoteNotFoundError
from app.services.knowledge_ingest import KnowledgeIngestService
from app.services.rag_service import RAGService

router = APIRouter(prefix="/api")


@lru_cache
def get_rag_service() -> RAGService:
    """Singleton dùng chung cho mọi request (1 SupabaseClient, 1 embedding/LLM provider)."""
    return RAGService(get_settings())


def get_admin_service(rag: RAGService = Depends(get_rag_service)) -> AdminService:
    return rag.admin


def require_admin(
    settings: Settings = Depends(get_settings),
    x_admin_token: str | None = Header(default=None),
) -> None:
    if not settings.admin_token:
        raise HTTPException(status_code=503, detail="ADMIN_TOKEN chưa được cấu hình trong .env")
    if not x_admin_token or not secrets.compare_digest(x_admin_token, settings.admin_token):
        raise HTTPException(status_code=401, detail="Admin token không hợp lệ")


@router.get("/health")
def health(rag: RAGService = Depends(get_rag_service)) -> dict[str, str | int]:
    notes = rag.admin.list_notes("all")
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


@router.post("/chat-staff", response_model=ChatResponse)
def chat_staff(request: ChatRequest, rag: RAGService = Depends(get_rag_service)) -> ChatResponse:
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
    try:
        rag.supabase.insert("chat_logs", [{
            "question": request.question,
            "answer": result["answer"],
            "grounded": result["grounded"],
            "citations": result["citations"],
        }], returning=False)
    except Exception:
        pass  # log chat là best-effort, không được làm hỏng câu trả lời cho user
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


@router.post("/admin/notes/create", dependencies=[Depends(require_admin)])
def admin_create_note(body: NoteCreateRequest, admin: AdminService = Depends(get_admin_service)) -> dict[str, object]:
    note = admin.create_note(body.title, body.department, body.content, body.access_level, body.status)
    return {"status": "created", "id": note["id"]}


@router.post("/admin/upload", dependencies=[Depends(require_admin)])
async def admin_upload_knowledge(
    file: UploadFile = File(...),
    department: str = Form("Unassigned"),
    title: str | None = Form(None),
    admin: AdminService = Depends(get_admin_service),
) -> dict[str, object]:
    content = await file.read()
    try:
        return KnowledgeIngestService(admin).ingest(file.filename or "upload", content, department, title)
    except ValueError as error:
        raise HTTPException(status_code=400, detail=str(error)) from error
    except Exception as error:
        raise HTTPException(status_code=500, detail=f"Không thể ghi dữ liệu vào Supabase: {error}") from error
