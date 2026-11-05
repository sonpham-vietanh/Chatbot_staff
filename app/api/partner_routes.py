"""API cho app lương (xem docs/tich-hop-app-luong.md). Mọi endpoint /api/partner/payroll/* đều yêu cầu chữ ký HMAC của backend app lương."""
from __future__ import annotations

import logging
from typing import Annotated, Any, TypeVar

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, Query, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel, ConfigDict, Field, StringConstraints, ValidationError

from app.api.routes import (
    NO_STORE, _asker_context, _llm_failure, _log_chat, _own_info_answer, _track, _with_no_repeat_note, get_rag_service, require_admin,
)
from app.models.schemas import ChatRequest, ChatResponse
from app.rag.answer_format import to_plain_text
from app.rag.suggestions import suggest_followups
from app.services.directory_service import EMAIL_RE, normalize_email
from app.services.payroll_partner_service import (
    ARTICLE_ID_RE, MAX_ARTICLE_CHARS, MAX_TITLE_CHARS, PartnerError,
)
from app.services.rag_service import RAGService

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api")
PARTNER_SOURCE = "app-luong"
MAX_SNAPSHOT_PAGE = 200

M = TypeVar("M", bound=BaseModel)


class PayrollChatRequest(ChatRequest):
    email: str = Field(min_length=3, max_length=200, description="Email công ty của nhân viên đang đăng nhập app lương.")


class ArticleIn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    id: str = Field(description="Mã bài ổn định bên app lương (chữ, số, . _ -, tối đa 64 ký tự). Gửi lại cùng mã = cập nhật bài.")
    title: Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=MAX_TITLE_CHARS)]
    content: Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=MAX_ARTICLE_CHARS)]


class KnowledgePush(BaseModel):
    model_config = ConfigDict(extra="forbid")
    articles: list[ArticleIn] = []
    delete_ids: list[str] = []


class EmployeesPush(BaseModel):
    employees: list[dict[str, Any]]


def get_payroll(rag: RAGService = Depends(get_rag_service)):
    service = getattr(rag, "payroll", None)
    if service is None:
        raise HTTPException(status_code=503, detail="Kết nối app lương chưa sẵn sàng.")
    return service


async def signed_body(request: Request, payroll=Depends(get_payroll)) -> bytes:
    body = await request.body()
    try:
        payroll.verify(request.headers.get("x-timestamp"), request.headers.get("x-signature"), request.method, request.url.path, body)
    except PartnerError as error:
        raise HTTPException(status_code=error.status_code, detail=error.detail) from error
    return body


def parse_body(model: type[M], body: bytes) -> M:
    try:
        return model.model_validate_json(body)
    except ValidationError as error:
        first = error.errors()[0]
        where = ".".join(str(part) for part in first.get("loc", ()))
        raise HTTPException(status_code=422, detail=f"Nội dung không hợp lệ ({where}): {first.get('msg')}") from error


def _check_ids(ids: list[str]) -> None:
    bad = [i for i in ids if not ARTICLE_ID_RE.match(i)]
    if bad:
        raise HTTPException(status_code=422, detail=f"Mã bài không hợp lệ: {bad[0][:40]!r} (chỉ chữ, số, '.', '_', '-', tối đa 64 ký tự).")


@router.get("/partner/payroll/ping")
def payroll_ping(_body: bytes = Depends(signed_body)) -> JSONResponse:
    return JSONResponse({"ok": True, "app": "Trợ lý nội bộ Trường Việt Anh"}, headers=NO_STORE)


@router.post("/partner/payroll/chat", response_model=ChatResponse)
def payroll_chat(
    background_tasks: BackgroundTasks,
    body: bytes = Depends(signed_body),
    rag: RAGService = Depends(get_rag_service),
    payroll=Depends(get_payroll),
) -> JSONResponse:
    """Bong bóng chat trong app lương. Backend app lương đã xác thực nhân viên và ký kèm email; bot kiểm tra người đó còn trong danh bạ."""
    request = parse_body(PayrollChatRequest, body)
    email = normalize_email(request.email)
    if not EMAIL_RE.match(email):
        raise HTTPException(status_code=422, detail="Email không hợp lệ.")
    row = rag.directory.get(email)
    if not row or not row.get("active"):
        raise HTTPException(status_code=403, detail="Nhân viên này không có trong danh bạ hoặc đã ngừng hoạt động.")
    if not payroll.allow_chat(email):
        raise HTTPException(status_code=429, detail="Bạn hỏi hơi nhanh, vui lòng thử lại sau ít giây.", headers={"Retry-After": "10"})
    user = {"id": None, "email": email, "employee_profile": {"display_name": row.get("display_name"), "department": row.get("department")},
            "user_metadata": {}}
    turns = [turn.model_dump() for turn in request.history]
    result = _own_info_answer(rag, user, request.question)
    if result is None:
        quick = getattr(rag, "_quick_reply", None)
        asker = None if (quick is not None and quick(request.question) is not None) else _asker_context(rag, user)
        asker = _with_no_repeat_note(asker, turns, request.question, quick)
        extra = {"asker": asker} if asker else {}
        try:
            result = rag.chat(request.question, turns, asker_department=row.get("department"), **extra)
        except Exception as error:
            raise _llm_failure(error) from error
    result = {**result, "answer": to_plain_text(result["answer"])}
    result["suggestions"] = suggest_followups(request.question, result["answer"])
    background_tasks.add_task(_log_chat, rag.supabase, request.question, result, email)
    _track(background_tasks, rag, "hoi_dap", user, {"tra_loi_duoc": bool(result["grounded"]), "so_nguon": len(result["citations"]), "nguon": PARTNER_SOURCE})
    return JSONResponse(ChatResponse(**result).model_dump(mode="json"), headers=NO_STORE)


@router.put("/partner/payroll/knowledge")
def payroll_push_knowledge(body: bytes = Depends(signed_body), payroll=Depends(get_payroll)) -> JSONResponse:
    """App lương đẩy bài hướng dẫn dùng app. Chỉ nội dung hướng dẫn chung — KHÔNG gửi số liệu lương của từng người."""
    push = parse_body(KnowledgePush, body)
    _check_ids([a.id for a in push.articles] + push.delete_ids)
    try:
        result = payroll.push_articles([a.model_dump() for a in push.articles], push.delete_ids)
    except PartnerError as error:
        raise HTTPException(status_code=error.status_code, detail=error.detail) from error
    return JSONResponse(result, headers=NO_STORE)


@router.post("/partner/payroll/employees")
def payroll_update_employees(body: bytes = Depends(signed_body), payroll=Depends(get_payroll)) -> JSONResponse:
    """App lương sửa ngược một số trường hồ sơ (ngày vào làm, loại hợp đồng, hình thức, bậc). Không tạo người mới."""
    push = parse_body(EmployeesPush, body)
    try:
        result = payroll.update_employees(push.employees)
    except PartnerError as error:
        raise HTTPException(status_code=error.status_code, detail=error.detail) from error
    return JSONResponse(result, headers=NO_STORE)


@router.get("/partner/payroll/directory")
def payroll_snapshot(
    offset: int = Query(0, ge=0),
    limit: int = Query(100, ge=1, le=MAX_SNAPSHOT_PAGE),
    _body: bytes = Depends(signed_body),
    payroll=Depends(get_payroll),
) -> JSONResponse:
    """Toàn bộ danh bạ (cả người đã nghỉ, có cờ `active`) theo trang: để nạp lần đầu và đồng bộ bù khi lỡ webhook."""
    return JSONResponse(payroll.snapshot(offset, limit), headers=NO_STORE)


@router.post("/admin/payroll-webhook/test", dependencies=[Depends(require_admin)])
def admin_payroll_webhook_test(rag: RAGService = Depends(get_rag_service)) -> JSONResponse:
    """Gửi 1 sự kiện thử `thu_ket_noi` tới PAYROLL_WEBHOOK_URL; trả mã phản hồi (không lộ khoá hay địa chỉ đầy đủ)."""
    webhook = getattr(rag, "payroll_webhook", None)
    if webhook is None or not webhook.enabled:
        raise HTTPException(status_code=409, detail="Chưa cấu hình PAYROLL_WEBHOOK_URL và PAYROLL_WEBHOOK_SECRET.")
    status = webhook.send_test()
    return JSONResponse({"ok": status is not None and 200 <= status < 300, "ma_phan_hoi": status,
                         "ghi_chu": None if status is not None else "Không kết nối được tới app lương (lỗi mạng/quá thời gian)."}, headers=NO_STORE)
