import secrets

from fastapi import APIRouter, Depends, File, Form, Header, HTTPException, Query, UploadFile
import frontmatter

from app.config import Settings, get_settings
from app.models.schemas import ChatRequest, ChatResponse, NoteUpdateRequest, ReindexResponse, SearchResult
from app.services.admin_service import AdminService, InvalidNotePathError, NoteNotFoundError
from app.services.rag_service import RAGService
from app.services.knowledge_ingest import KnowledgeIngestService

router = APIRouter(prefix="/api")


def get_rag_service(settings: Settings = Depends(get_settings)) -> RAGService:
    return RAGService(settings)


def get_admin_service(settings: Settings = Depends(get_settings)) -> AdminService:
    return AdminService(settings.obsidian_vault_path)


def require_admin(
    settings: Settings = Depends(get_settings),
    x_admin_token: str | None = Header(default=None),
) -> None:
    if not settings.admin_token:
        raise HTTPException(status_code=503, detail="ADMIN_TOKEN chưa được cấu hình trong .env")
    if not x_admin_token or not secrets.compare_digest(x_admin_token, settings.admin_token):
        raise HTTPException(status_code=401, detail="Admin token không hợp lệ")


def require_knowledge_editor(user_department: str, user_access_level: str) -> None:
    if user_department.casefold() != "hr" and user_access_level.casefold() != "admin":
        raise HTTPException(status_code=403, detail="Chỉ HR hoặc admin được upload dữ liệu vào Obsidian")


@router.get("/health")
def health(settings: Settings = Depends(get_settings)) -> dict[str, str | int]:
    markdown_files = list(settings.obsidian_vault_path.rglob("*.md")) if settings.obsidian_vault_path.is_dir() else []
    approved_count = 0
    for path in markdown_files:
        try:
            if str(frontmatter.load(path).metadata.get("status", "")).lower() == "approved":
                approved_count += 1
        except (OSError, UnicodeDecodeError):
            continue
    return {
        "status": "ok",
        "service": "viet-anh-staff-assistant",
        "vault_path": str(settings.obsidian_vault_path),
        "vault_markdown_files": len(markdown_files),
        "vault_approved_files": approved_count,
    }


@router.post("/reindex", response_model=ReindexResponse)
def reindex(rag: RAGService = Depends(get_rag_service)) -> ReindexResponse:
    stats = rag.reindex()
    return ReindexResponse(
        indexed_files=stats.indexed_files,
        indexed_chunks=stats.indexed_chunks,
        graph_edges=stats.graph_edges,
    )


@router.post("/knowledge/upload")
async def upload_knowledge(
    file: UploadFile = File(...),
    user_department: str = Form(...),
    user_access_level: str = Form("staff"),
    title: str | None = Form(None),
    settings: Settings = Depends(get_settings),
) -> dict[str, object]:
    require_knowledge_editor(user_department, user_access_level)
    content = await file.read()
    try:
        return KnowledgeIngestService(settings.obsidian_vault_path).ingest(
            file.filename or "upload", content, user_department, title
        )
    except ValueError as error:
        raise HTTPException(status_code=400, detail=str(error)) from error
    except Exception as error:
        raise HTTPException(status_code=500, detail=f"Không thể ghi dữ liệu vào Obsidian: {error}") from error


@router.get("/debug/search", response_model=list[SearchResult])
def debug_search(
    q: str = Query(min_length=2),
    user_department: str | None = None,
    user_access_level: str = "staff",
    version: str | None = None,
    department: str | None = None,
    access_level: str | None = None,
    rag: RAGService = Depends(get_rag_service),
) -> list[SearchResult]:
    return rag.search(q, user_department or department, access_level or user_access_level, version)


@router.post("/chat-staff", response_model=ChatResponse)
def chat_staff(request: ChatRequest, rag: RAGService = Depends(get_rag_service)) -> ChatResponse:
    try:
        return ChatResponse(**rag.chat(
            request.question,
            request.user_department or request.department,
            request.access_level or request.user_access_level,
            request.version,
            [turn.model_dump() for turn in request.history],
        ))
    except Exception as error:
        message = str(error)
        if "API_KEY_INVALID" in message or "API key not valid" in message:
            raise HTTPException(
                status_code=502,
                detail="Gemini API key không hợp lệ hoặc đã bị thu hồi. Hãy cập nhật GEMINI_API_KEY trong .env rồi restart backend.",
            ) from error
        raise HTTPException(status_code=502, detail="Gemini hiện không thể xử lý yêu cầu. Kiểm tra log backend.") from error


@router.get("/admin/notes", dependencies=[Depends(require_admin)])
def admin_list_notes(
    status: str = Query("draft"),
    admin: AdminService = Depends(get_admin_service),
) -> list[dict[str, object]]:
    return admin.list_notes(status)


@router.get("/admin/notes/detail", dependencies=[Depends(require_admin)])
def admin_get_note(path: str, admin: AdminService = Depends(get_admin_service)) -> dict[str, object]:
    try:
        return admin.get_note(path)
    except NoteNotFoundError as error:
        raise HTTPException(status_code=404, detail=str(error)) from error
    except InvalidNotePathError as error:
        raise HTTPException(status_code=400, detail=str(error)) from error


@router.put("/admin/notes", dependencies=[Depends(require_admin)])
def admin_update_note(
    path: str,
    body: NoteUpdateRequest,
    admin: AdminService = Depends(get_admin_service),
) -> dict[str, str]:
    try:
        admin.save_note(path, body.metadata, body.content)
    except NoteNotFoundError as error:
        raise HTTPException(status_code=404, detail=str(error)) from error
    except InvalidNotePathError as error:
        raise HTTPException(status_code=400, detail=str(error)) from error
    return {"status": "saved"}


@router.post("/admin/notes/approve", dependencies=[Depends(require_admin)])
def admin_approve_note(path: str, admin: AdminService = Depends(get_admin_service)) -> dict[str, str]:
    try:
        admin.set_status(path, "approved")
    except NoteNotFoundError as error:
        raise HTTPException(status_code=404, detail=str(error)) from error
    except InvalidNotePathError as error:
        raise HTTPException(status_code=400, detail=str(error)) from error
    return {"status": "approved"}


@router.post("/admin/notes/reject", dependencies=[Depends(require_admin)])
def admin_reject_note(path: str, admin: AdminService = Depends(get_admin_service)) -> dict[str, str]:
    try:
        admin.set_status(path, "rejected")
    except NoteNotFoundError as error:
        raise HTTPException(status_code=404, detail=str(error)) from error
    except InvalidNotePathError as error:
        raise HTTPException(status_code=400, detail=str(error)) from error
    return {"status": "rejected"}


@router.delete("/admin/notes", dependencies=[Depends(require_admin)])
def admin_delete_note(path: str, admin: AdminService = Depends(get_admin_service)) -> dict[str, str]:
    try:
        admin.delete_note(path)
    except NoteNotFoundError as error:
        raise HTTPException(status_code=404, detail=str(error)) from error
    except InvalidNotePathError as error:
        raise HTTPException(status_code=400, detail=str(error)) from error
    return {"status": "deleted"}
