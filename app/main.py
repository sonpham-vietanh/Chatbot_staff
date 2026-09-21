from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI
from fastapi.middleware.gzip import GZipMiddleware
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

from app.api.routes import get_rag_service, router
from app.config import get_settings

FRONTEND_DIST = Path(__file__).parent.parent / "frontend" / "dist"


@asynccontextmanager
async def lifespan(app: FastAPI):
    get_settings().validate_supabase()
    yield


app = FastAPI(
    title="Viet Anh Staff Assistant",
    description="RAG nội bộ dựa trên tri thức đã được duyệt, lưu trên Supabase.",
    version="0.2.0",
    lifespan=lifespan,
)
app.include_router(router)
app.add_middleware(GZipMiddleware, minimum_size=500)
"""Nén JS/CSS/JSON trước khi gửi — bundle React ~245KB chưa nén còn ~77KB sau gzip.
Máy chưa có cache trình duyệt (lần đầu mở) phải tải nguyên file này, nên đây là chỗ
ảnh hưởng trực tiếp tới thời gian mở trang đầu tiên."""

if (FRONTEND_DIST / "assets").is_dir():
    app.mount("/assets", StaticFiles(directory=FRONTEND_DIST / "assets"), name="frontend-assets")


@app.get("/", include_in_schema=False)
def demo_ui(embed_key: str | None = None) -> FileResponse:
    """Ở production (Docker), frontend/dist đã được build sẵn nên phục vụ luôn React app thật;
    ở local dev (chưa build) fallback về trang demo tĩnh để không phá luồng chạy hiện tại.

    Mặc định chặn nhúng (frame-ancestors 'self') để tránh site lạ tự ý iframe trang này —
    chỉ khi có ?embed_key= khớp 1 key active trong bảng api_keys mới cho phép đúng domain
    đã đăng ký của key đó nhúng vào."""
    file_path = FRONTEND_DIST / "index.html" if (FRONTEND_DIST / "index.html").is_file() else Path(__file__).parent / "static" / "index.html"
    response = FileResponse(file_path)
    frame_ancestors = "'self'"
    if embed_key:
        record = get_rag_service().api_keys.get_active_key(embed_key)
        if record:
            frame_ancestors = f"'self' {record['allowed_origin']}"
            get_rag_service().api_keys.touch_last_used(record["id"])
    response.headers["Content-Security-Policy"] = f"frame-ancestors {frame_ancestors}"
    return response


@app.get("/admin", include_in_schema=False)
def admin_ui() -> FileResponse:
    response = FileResponse(Path(__file__).parent / "static" / "admin.html")
    response.headers["Content-Security-Policy"] = "frame-ancestors 'self'"
    return response
