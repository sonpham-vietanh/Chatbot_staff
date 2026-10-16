import threading
import logging
import mimetypes
from functools import lru_cache
from urllib.parse import quote, urlencode
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.gzip import GZipMiddleware
from fastapi.responses import FileResponse, JSONResponse, RedirectResponse
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

LEXICAL_REFRESH_INTERVAL_SECONDS = 40.0
"""Nhỏ hơn TTL 45s của LexicalStore (app/rag/lexical_store.py) — làm mới chỉ mục ở nền
TRƯỚC khi hết hạn, để không có request chat nào của người dùng phải chờ xây lại chỉ mục
(đọc lại toàn bộ knowledge_notes/knowledge_chunks) ngay giữa lúc đang trả lời."""


def _refresh_lexical_index_periodically(warm, stop_event: threading.Event) -> None:
    while not stop_event.wait(LEXICAL_REFRESH_INTERVAL_SECONDS):
        try:
            warm()
        except Exception:
            logging.getLogger(__name__).warning("Không làm mới được chỉ mục tìm kiếm ở nền", exc_info=True)


@asynccontextmanager
async def lifespan(app: FastAPI):
    get_settings().validate_supabase()

    watcher: VaultWatcher | None = None
    stop_lexical_refresh = threading.Event()
    rag = get_rag_service()
    warm = getattr(rag.vector_store, "warm", None)
    if warm is not None:  # nạp chỉ mục tìm kiếm ở luồng nền để không chặn khởi động và người dùng đầu tiên không phải chờ
        threading.Thread(target=warm, name="warm-lexical-index", daemon=True).start()
        threading.Thread(
            target=_refresh_lexical_index_periodically, args=(warm, stop_lexical_refresh),
            name="refresh-lexical-index", daemon=True,
        ).start()
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

    stop_lexical_refresh.set()
    if watcher is not None:
        await watcher.stop()


app = FastAPI(
    title="Viet Anh Staff Assistant",
    description="RAG nội bộ dựa trên tri thức đã được duyệt, lưu trên Supabase.",
    version="0.2.0",
    lifespan=lifespan,
)
app.include_router(router)

FIELD_LABELS = {
    "question": "Câu hỏi", "history": "Lịch sử hội thoại", "thread_id": "Mã cuộc trò chuyện", "title": "Tiêu đề", "content": "Nội dung",
    "department": "Phòng ban", "status": "Trạng thái", "access_level": "Quyền xem", "email": "Email", "password": "Mật khẩu",
    "display_name": "Họ tên", "phone": "Số điện thoại", "birth_date": "Ngày sinh", "gender": "Giới tính", "bio": "Giới thiệu",
    "metric_name": "Tên chỉ số", "period_label": "Kỳ", "target_value": "Mục tiêu", "start_value": "Giá trị đầu kỳ", "due_date": "Hạn",
    "value": "Giá trị", "note": "Ghi chú", "reason": "Lý do", "new_value": "Giá trị mới", "field": "Trường",
}


def _validation_message(errors: list[dict]) -> str:
    """Đổi lỗi kiểm tra dữ liệu (pydantic, tiếng Anh) thành câu tiếng Việt ngắn. Không trả lại dữ liệu người dùng đã gửi
    (một câu hỏi 200.000 ký tự không được phản hồi lại nguyên khối)."""
    parts = []
    for error in errors[:3]:
        path = [str(p) for p in error.get("loc", ()) if p not in ("body", "query", "path", "header") and not str(p).isdigit()]
        name = FIELD_LABELS.get(path[-1], path[-1]) if path else "Dữ liệu"
        kind, ctx = error.get("type", ""), error.get("ctx") or {}
        if kind == "missing":
            reason = "đang thiếu"
        elif kind == "string_too_short":
            reason = "chưa đủ độ dài" if ctx.get("min_length", 1) > 1 else "không được để trống"
        elif kind == "string_too_long":
            reason = f"quá dài (tối đa {ctx.get('max_length')} ký tự)"
        elif kind in ("too_short", "too_long"):
            reason = "có số mục không hợp lệ"
        elif kind in ("json_invalid", "model_attributes_type"):
            return "Dữ liệu gửi lên không đúng định dạng."
        elif kind == "uuid_parsing" or kind == "string_pattern_mismatch":
            reason = "không đúng định dạng"
        else:
            reason = "không hợp lệ"
        parts.append(f"{name} {reason}")
    return "Dữ liệu chưa hợp lệ: " + "; ".join(parts) + "."


@app.exception_handler(RequestValidationError)
async def validation_error_handler(_request: Request, error: RequestValidationError) -> JSONResponse:
    return JSONResponse(status_code=422, content={"detail": _validation_message(error.errors())})


@app.middleware("http")
async def security_headers(request: Request, call_next):
    response = await call_next(request)
    response.headers.setdefault("X-Content-Type-Options", "nosniff")
    response.headers.setdefault("Referrer-Policy", "strict-origin-when-cross-origin")
    if request.headers.get("x-forwarded-proto") == "https":
        response.headers.setdefault("Strict-Transport-Security", "max-age=31536000")
    return response
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

