import logging
import mimetypes
from functools import lru_cache
from urllib.parse import quote, urlencode
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI
from fastapi.middleware.gzip import GZipMiddleware
from fastapi.responses import FileResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from starlette.background import BackgroundTask

from app.api.routes import get_rag_service, router
from app.config import get_settings
from app.services.api_key_service import normalize_origin
from app.services.sso_service import SsoError, SsoService
from app.services.vault_watcher import VaultWatcher

# Mac dinh Python khong co handler nao ca - log INFO cua vault_watcher (vd xac nhan
# lock, ket qua ingest) se bi am tham bo qua neu khong bat dong nay len (da xac
# nhan qua test container that: khong co dong log nao xuat hien du code co chay).
logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")

FRONTEND_DIST = Path(__file__).parent.parent / "frontend" / "dist"
NO_CACHE = "no-cache"
"""Trang HTML phải được trình duyệt hỏi lại mỗi lần mở: sau mỗi lần build, tên file
/assets/index-<hash>.css|js đổi, trang HTML cũ còn trong cache sẽ trỏ tới file không còn
tồn tại -> giao diện mất hết CSS. (File trong /assets có hash trong tên nên cache thoải mái.)"""


@asynccontextmanager
async def lifespan(app: FastAPI):
    get_settings().validate_supabase()

    watcher: VaultWatcher | None = None
    rag = get_rag_service()
    if rag.ingest_agent is not None and rag.wiki_sync is not None:
        watcher = VaultWatcher(
            vault_path=rag.settings.vault_path,
            ingest_agent=rag.ingest_agent,
            wiki_sync=rag.wiki_sync,
            state_path=Path("data") / "vault_watcher_state.json",
            ready_marker=rag.settings.vault_ready_marker,
        )
        watcher.start()

    yield

    if watcher is not None:
        await watcher.stop()


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
# Logo + favicon chính thức (Brand Guideline Trường Việt Anh) dùng chung cho giao diện chat
# React, trang /admin và trang dự phòng — 1 bản duy nhất trong app/static/brand. Bảng
# mimetypes của Python không phải máy nào cũng có .webp (Windows trả octet-stream).
mimetypes.add_type("image/webp", ".webp")
app.mount("/brand", StaticFiles(directory=Path(__file__).parent / "static" / "brand"), name="brand")


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
        try:
            record = get_rag_service().api_keys.get_active_key(embed_key)
        except Exception:
            # Database tạm lỗi: vẫn trả trang (chỉ là không cho nhúng), không trả 500.
            logging.getLogger(__name__).warning("Không tra được embed key", exc_info=True)
            record = None
        allowed_origin = normalize_origin(record["allowed_origin"]) if record else None
        if allowed_origin:
            frame_ancestors = f"'self' {allowed_origin}"
            get_rag_service().api_keys.touch_last_used(record["id"])
    response.headers["Content-Security-Policy"] = f"frame-ancestors {frame_ancestors}"
    response.headers["Cache-Control"] = NO_CACHE
    return response


@lru_cache
def get_sso_service() -> SsoService:
    settings = get_settings()
    return SsoService(settings.supabase_url, settings.supabase_service_key, settings.supabase_anon_key,
                      settings.os_sso_secret, settings.allowed_email_domains)


def _track_sso_login(email: str) -> None:
    try:
        get_rag_service().usage.log("dang_nhap_os", {"email": email})
    except Exception:
        logging.getLogger(__name__).warning("Không ghi được sự kiện đăng nhập từ Major OS", exc_info=True)


@app.get("/sso/os", include_in_schema=False)
def sso_from_major_os(token: str = "") -> RedirectResponse:
    """Đăng nhập 1 lần từ Major OS (xem app/services/sso_service.py). Phiên trả về qua URL
    fragment để token không bao giờ đi lên server/log; giao diện React tự đọc rồi xoá đi."""
    sso = get_sso_service()
    try:
        email = sso.verify_token(token)
        session = sso.create_session(email)
    except SsoError as error:
        logging.getLogger(__name__).warning("SSO Major OS bị từ chối: %s", error)
        response = RedirectResponse(f"/?sso_error={quote(str(error))}", status_code=303)
    else:
        fragment = urlencode({"sso_access": session["access_token"], "sso_refresh": session["refresh_token"]})
        response = RedirectResponse(f"/#{fragment}", status_code=303, background=BackgroundTask(_track_sso_login, email))
    response.headers["Cache-Control"] = "no-store"
    response.headers["Referrer-Policy"] = "no-referrer"
    return response


@app.get("/ho-so", include_in_schema=False)
def profile_ui() -> FileResponse:
    """Trang Hồ sơ & WIG — cùng ứng dụng React; dữ liệu đi qua /api/me/* nên quyền được kiểm tra ở API."""
    return manage_ui()


@app.get("/quan-ly", include_in_schema=False)
def manage_ui() -> FileResponse:
    """Trang quản lý tri thức cho leader — cùng ứng dụng React với trang chat (App.jsx chọn màn hình
    theo đường dẫn); quyền được kiểm tra ở API /api/manage/*, không phải ở đây."""
    index = FRONTEND_DIST / "index.html"
    response = FileResponse(index if index.is_file() else Path(__file__).parent / "static" / "index.html")
    response.headers["Content-Security-Policy"] = "frame-ancestors 'self'"
    response.headers["Cache-Control"] = NO_CACHE
    return response


@app.get("/admin", include_in_schema=False)
def admin_ui() -> FileResponse:
    response = FileResponse(Path(__file__).parent / "static" / "admin.html")
    response.headers["Content-Security-Policy"] = "frame-ancestors 'self'"
    response.headers["Cache-Control"] = NO_CACHE
    return response

