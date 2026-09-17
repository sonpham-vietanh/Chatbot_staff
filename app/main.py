from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI
from fastapi.responses import FileResponse

from app.api.routes import router
from app.config import get_settings
from app.rag.pipeline import AdvancedRAGPipeline
from app.services.vault_watcher import VaultWatcher


@asynccontextmanager
async def lifespan(app: FastAPI):
    settings = get_settings()
    settings.validate_vault_path()
    settings.draft_review_path.mkdir(parents=True, exist_ok=True)
    settings.vector_db_path.mkdir(parents=True, exist_ok=True)
    settings.graph_path.parent.mkdir(parents=True, exist_ok=True)
    watcher = None
    if settings.vault_watcher_enabled:
        pipeline = AdvancedRAGPipeline(settings)
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


@app.get("/", include_in_schema=False)
def demo_ui() -> FileResponse:
    return FileResponse(Path(__file__).parent / "static" / "index.html")


@app.get("/admin", include_in_schema=False)
def admin_ui() -> FileResponse:
    return FileResponse(Path(__file__).parent / "static" / "admin.html")
