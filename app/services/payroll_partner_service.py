"""Cầu nối với app lương (xem docs/tich-hop-app-luong.md).

Hai bên dùng chung hồ sơ nhân sự, bot là nguồn gốc:
- bot -> app lương: mỗi khi danh bạ đổi, bot đẩy webhook (qua WebhookService, có thử lại + ngắt mạch) và có API snapshot để đồng bộ bù;
- app lương -> bot: chỉ được sửa vài trường trong danh sách trắng, và được đẩy bài hướng dẫn dùng app vào kho tri thức;
- bong bóng chat trong app lương gọi chat qua backend app lương, ký HMAC kèm email nhân viên.
Mọi yêu cầu vào bot đều ký HMAC-SHA256 trên (thời điểm, phương thức, đường dẫn, body thô) nên không phát lại được và không dùng chéo endpoint."""
from __future__ import annotations

import hashlib
import hmac
import logging
import re
import threading
import time
import uuid
from datetime import datetime, timedelta, timezone
from typing import Any

from app.services.directory_service import DirectoryError, clean_fields, normalize_email
from app.services.webhook_service import WebhookService

logger = logging.getLogger(__name__)

MIN_SECRET_LENGTH = 32
SKEW_SECONDS = 300
ACTOR = "payroll-app"
SOURCE_PREFIX = "partner:payroll:"
ARTICLE_DEPARTMENT = "Unassigned"
"""Bài hướng dẫn app lương dùng chung cho mọi nhân viên (cùng quy ước với nội dung dùng chung của wiki)."""
CREATED_BY = "PayrollApp"

OUTBOUND_FIELDS = ("email", "display_name", "department", "job_title", "employment_start_date", "employment_end_date", "active",
                   "manager_email", "campus", "employee_code", "level_name", "contract_type", "employment_type", "team_name")
"""Trường bot đẩy sang app lương. Cố ý không có số điện thoại, môn dạy, kiêm nhiệm: app lương không cần."""
INBOUND_EMPLOYEE_FIELDS = ("employment_start_date", "contract_type", "employment_type", "level_name")
"""Trường duy nhất app lương được phép sửa ngược vào danh bạ của bot."""

ARTICLE_ID_RE = re.compile(r"^[A-Za-z0-9._-]{1,64}$")
MAX_ARTICLES = 50
MAX_DELETE_IDS = 50
MAX_TITLE_CHARS = 200
MAX_ARTICLE_CHARS = 20_000
MAX_EMPLOYEE_ROWS = 200
WEBHOOK_BATCH = 100
CHAT_LIMIT_PER_MINUTE = 20
CHAT_LIMIT_MAX_ENTRIES = 2000


class PartnerError(Exception):
    def __init__(self, status_code: int, detail: str):
        super().__init__(detail)
        self.status_code = status_code
        self.detail = detail


def signing_message(timestamp: str, method: str, path: str, body: bytes) -> bytes:
    return f"{timestamp}\n{method.upper()}\n{path}\n".encode() + body


def sign(secret: str, timestamp: str, method: str, path: str, body: bytes) -> str:
    return "sha256=" + hmac.new(secret.encode(), signing_message(timestamp, method, path, body), hashlib.sha256).hexdigest()


def _now_vn() -> str:
    return datetime.now(timezone(timedelta(hours=7))).isoformat()


class PayrollPartnerService:
    def __init__(self, supabase: Any, admin: Any, directory: Any, webhook: WebhookService | None, inbound_secret: str | None):
        self.supabase = supabase
        self.admin = admin
        self.directory = directory
        self.webhook = webhook
        self.secret = (inbound_secret or "").strip() or None
        self._chat_hits: dict[str, list[float]] = {}
        self._lock = threading.Lock()

    @property
    def enabled(self) -> bool:
        return bool(self.secret and len(self.secret) >= MIN_SECRET_LENGTH)

    # ---------- xác thực yêu cầu từ app lương ----------
    def verify(self, timestamp: str | None, signature: str | None, method: str, path: str, body: bytes, *, now: float | None = None) -> None:
        if not self.enabled:
            raise PartnerError(503, "Kết nối app lương chưa được cấu hình (PAYROLL_INBOUND_SECRET, tối thiểu 32 ký tự).")
        try:
            stamp = int(str(timestamp or "").strip())
        except ValueError as error:
            raise PartnerError(401, "Thiếu hoặc sai X-Timestamp (giây Unix).") from error
        if abs((now if now is not None else time.time()) - stamp) > SKEW_SECONDS:
            raise PartnerError(401, "X-Timestamp lệch quá 5 phút so với giờ máy chủ.")
        expected = sign(self.secret or "", str(stamp), method, path, body)
        if not hmac.compare_digest(expected, signature or ""):
            logger.warning("Chữ ký app lương không hợp lệ cho %s %s", method, path)
            raise PartnerError(401, "Chữ ký không hợp lệ.")

    def allow_chat(self, email: str) -> bool:
        """Tối đa CHAT_LIMIT_PER_MINUTE câu mỗi người mỗi phút để bong bóng chat không làm nghẽn mô hình dùng chung."""
        now = time.monotonic()
        with self._lock:
            if len(self._chat_hits) >= CHAT_LIMIT_MAX_ENTRIES:
                for key in [k for k, hits in self._chat_hits.items() if not hits or now - hits[-1] > 60]:
                    del self._chat_hits[key]
                if len(self._chat_hits) >= CHAT_LIMIT_MAX_ENTRIES:
                    self._chat_hits.clear()
            hits = [t for t in self._chat_hits.get(email, []) if now - t < 60]
            if len(hits) >= CHAT_LIMIT_PER_MINUTE:
                self._chat_hits[email] = hits
                return False
            hits.append(now)
            self._chat_hits[email] = hits
            return True

    # ---------- bot -> app lương ----------
    @staticmethod
    def outbound_row(row: dict[str, Any]) -> dict[str, Any]:
        return {key: row.get(key) for key in OUTBOUND_FIELDS}

    def push_employees(self, rows: list[dict[str, Any]]) -> None:
        """Gọi sau khi danh bạ đổi. Không bao giờ ném lỗi và không chặn việc của HR: gửi nền qua WebhookService."""
        try:
            if self.webhook is None or not self.webhook.enabled or not rows:
                return
            clean = [self.outbound_row(r) for r in rows if r.get("email")]
            for start in range(0, len(clean), WEBHOOK_BATCH):
                self.webhook.notify({
                    "id": str(uuid.uuid4()),
                    "tinh_nang": "nhan_vien_cap_nhat",
                    "thoi_diem": _now_vn(),
                    "nguoi_dung": None,
                    "so_luong": len(clean[start:start + WEBHOOK_BATCH]),
                    "chi_tiet": {"nhan_vien": clean[start:start + WEBHOOK_BATCH]},
                })
        except Exception:
            logger.warning("Không đẩy được webhook danh bạ sang app lương", exc_info=True)

    def snapshot(self, offset: int, limit: int) -> dict[str, Any]:
        page = self.directory.list("", "all", limit, offset)
        return {"tong": page["total"], "offset": offset, "nhan_vien": [self.outbound_row(r) for r in page["items"]]}

    # ---------- app lương -> bot: bài hướng dẫn ----------
    def push_articles(self, articles: list[dict[str, str]], delete_ids: list[str]) -> dict[str, Any]:
        if len(articles) > MAX_ARTICLES or len(delete_ids) > MAX_DELETE_IDS:
            raise PartnerError(422, f"Mỗi lần gửi tối đa {MAX_ARTICLES} bài và {MAX_DELETE_IDS} mã xoá.")
        existing = {n["source_file"]: n for n in self.supabase.select("knowledge_notes", {
            "select": "id,title,content,department,status,source_file", "source_file": f"like.{SOURCE_PREFIX}*", "limit": "5000"})}
        result: dict[str, Any] = {"created": 0, "updated": 0, "unchanged": 0, "deleted": 0, "errors": []}
        seen: set[str] = set()
        for article in articles:
            article_id = article["id"]
            if article_id in seen:
                result["errors"].append({"id": article_id, "message": "Mã bài bị trùng trong cùng lần gửi."})
                continue
            seen.add(article_id)
            source = SOURCE_PREFIX + article_id
            current = existing.get(source)
            try:
                if current is None:
                    self.admin.create_note(title=article["title"], department=ARTICLE_DEPARTMENT, content=article["content"],
                                           access_level="staff", status="approved", created_by=CREATED_BY, source_file=source)
                    result["created"] += 1
                elif (current.get("title"), current.get("content"), current.get("department"), current.get("status")) == (
                        article["title"], article["content"], ARTICLE_DEPARTMENT, "approved"):
                    result["unchanged"] += 1
                else:
                    self.admin.save_note(current["id"], {"title": article["title"], "content": article["content"],
                                                         "department": ARTICLE_DEPARTMENT, "status": "approved"})
                    result["updated"] += 1
            except Exception:
                logger.warning("Không lưu được bài %s từ app lương", article_id, exc_info=True)
                result["errors"].append({"id": article_id, "message": "Không lưu được bài này."})
        for article_id in delete_ids:
            note = existing.get(SOURCE_PREFIX + article_id)
            if note is None:
                continue
            try:
                self.admin.delete_note(note["id"])
                result["deleted"] += 1
            except Exception:
                logger.warning("Không xoá được bài %s từ app lương", article_id, exc_info=True)
                result["errors"].append({"id": article_id, "message": "Không xoá được bài này."})
        return result

    # ---------- app lương -> bot: vài trường hồ sơ ----------
    def update_employees(self, rows: list[dict[str, Any]]) -> dict[str, Any]:
        if len(rows) > MAX_EMPLOYEE_ROWS:
            raise PartnerError(422, f"Mỗi lần gửi tối đa {MAX_EMPLOYEE_ROWS} nhân viên.")
        result: dict[str, Any] = {"updated": 0, "unchanged": 0, "errors": []}
        for number, raw in enumerate(rows, start=1):
            email = normalize_email(raw.get("email"))
            fields = {k: v for k, v in raw.items() if k in INBOUND_EMPLOYEE_FIELDS and v not in (None, "")}
            try:
                if not fields:
                    raise DirectoryError(422, "Không có trường nào được phép sửa (" + ", ".join(INBOUND_EMPLOYEE_FIELDS) + ").")
                before = self.directory.get(email)
                if before is None:
                    raise DirectoryError(404, "Chưa có trong danh bạ của bot. Thêm nhân viên mới phải làm ở bot (HR).")
                clean_fields(fields, allow_clear=False)  # báo lỗi định dạng ngay, kể cả khi trùng giá trị cũ
                after = self.directory.update(email, fields, actor=ACTOR, notify=False)  # không đẩy ngược lại app lương: tránh vòng lặp
                changed = any(str(before.get(k) or "") != str(after.get(k) or "") for k in fields)
                result["updated" if changed else "unchanged"] += 1
            except DirectoryError as error:
                result["errors"].append({"row": number, "email": email, "message": error.detail})
            except Exception:
                logger.warning("Không cập nhật được nhân viên %s từ app lương", email, exc_info=True)
                result["errors"].append({"row": number, "email": email, "message": "Lỗi hệ thống khi cập nhật."})
        return result
