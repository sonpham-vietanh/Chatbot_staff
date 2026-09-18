from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

from app.api.routes import get_rag_service, router
from app.config import get_settings
from app.services.vault_watcher import VaultWatcher

FRONTEND_DIST = Path(__file__).parent.parent / "frontend" / "dist"


@asynccontextmanager
async def lifespan(app: FastAPI):
    settings = get_settings()
    settings.validate_vault_path()
    settings.draft_review_path.mkdir(parents=True, exist_ok=True)
    settings.vector_db_path.mkdir(parents=True, exist_ok=True)
    settings.graph_path.parent.mkdir(parents=True, exist_ok=True)
    watcher = None
    if settings.vault_watcher_enabled:
        # Dùng chung singleton với API requests (get_rag_service) thay vì tự tạo pipeline
        # riêng — 2 pipeline riêng biệt nghĩa là 2 kết nối ChromaDB mở song song vào cùng
        # 1 file SQLite, dễ gây lock/mất dữ liệu index.
        pipeline = get_rag_service()
        watcher = VaultWatcher(
            settings.obsidian_vault_path,
            pipeline.reindex,
            settings.vault_watcher_debounce_seconds,
        )
        watcher.start()
        app.state.vault_watcher = watcher
    yield
    if watcher:
        watcher.stop()


app = FastAPI(
    title="Viet Anh Staff Assistant",
    description="RAG nội bộ dựa trên tri thức Obsidian đã được duyệt.",
    version="0.1.0",
    lifespan=lifespan,
)
app.include_router(router)

if (FRONTEND_DIST / "assets").is_dir():
    app.mount("/assets", StaticFiles(directory=FRONTEND_DIST / "assets"), name="frontend-assets")


@app.get("/", include_in_schema=False)
def demo_ui() -> FileResponse:
    """Ở production (Docker), frontend/dist đã được build sẵn nên phục vụ luôn React app thật;
    ở local dev (chưa build) fallback về trang demo tĩnh để không phá luồng chạy hiện tại."""
    if (FRONTEND_DIST / "index.html").is_file():
        return FileResponse(FRONTEND_DIST / "index.html")
    return FileResponse(Path(__file__).parent / "static" / "index.html")


@app.get("/admin", include_in_schema=False)
def admin_ui() -> FileResponse:
    return FileResponse(Path(__file__).parent / "static" / "admin.html")
