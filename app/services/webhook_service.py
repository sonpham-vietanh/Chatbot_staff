"""Webhook đẩy sự kiện sử dụng sang Major OS (xem docs/webhook-major-os.md).

Chỉ chạy khi có đủ OS_WEBHOOK_URL + OS_WEBHOOK_SECRET. Mọi lỗi đều bị nuốt (chỉ ghi log): webhook không bao giờ
được làm hỏng hay làm chậm việc người dùng đang làm — gửi nền trong luồng riêng, có ngắt mạch khi Major OS chết."""
from __future__ import annotations

import hashlib
import hmac
import json
import logging
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone
from typing import Any, Callable

import httpx

logger = logging.getLogger(__name__)

APP_NAME = "Trợ lý nội bộ Trường Việt Anh"
TIMEOUT_SECONDS = 5.0
RETRY_DELAYS = (1.0, 4.0)
"""Tối đa 3 lần gửi: lần đầu, thử lại sau 1s, thử lại sau 4s."""
BREAKER_FAILURES = 5
BREAKER_PAUSE_SECONDS = 300.0
MAX_PENDING = 200
"""Hàng chờ gửi tối đa; đầy (Major OS chậm) thì bỏ sự kiện mới thay vì ăn dần bộ nhớ. Major OS vẫn lấy bù được qua /api/report/su-kien."""
COALESCE_SECONDS = 300.0
COALESCE_MAX_ENTRIES = 2000


def sign(secret: str, body: bytes) -> str:
    return "sha256=" + hmac.new(secret.encode(), body, hashlib.sha256).hexdigest()


class WebhookService:
    def __init__(self, url: str | None, secret: str | None, *, sleep: Callable[[float], None] = time.sleep,
                 post: Callable[..., Any] | None = None, run_inline: bool = False):
        self.url = (url or "").strip() or None
        self.secret = (secret or "").strip() or None
        self._sleep = sleep
        self._post = post or httpx.post
        self._run_inline = run_inline  # chỉ cho test: chạy đồng bộ thay vì luồng nền
        self._pool = ThreadPoolExecutor(max_workers=2, thread_name_prefix="os-webhook")
        self._lock = threading.Lock()
        self._pending = 0
        self._failures = 0
        self._paused_until = 0.0
        self._coalesce: dict[str, list[float]] = {}  # khoá người dùng -> [lần gửi gần nhất, số phút đang dồn]

    @property
    def enabled(self) -> bool:
        return bool(self.url and self.secret)

    # ---- dựng nội dung ----
    @staticmethod
    def build_payload(event: dict[str, Any], server_time: str) -> dict[str, Any]:
        return {"su_kien": event, "ten_app": APP_NAME, "gio_may_chu": server_time}

    @staticmethod
    def now_vn() -> str:
        return datetime.now(timezone(timedelta(hours=7))).isoformat()

    # ---- gửi ----
    def notify(self, event: dict[str, Any], *, coalesce_key: str | None = None) -> None:
        """Xếp hàng gửi nền. Với coalesce_key (heartbeat): dồn số phút, mỗi COALESCE_SECONDS/người mới gửi 1 lần."""
        if not self.enabled:
            return
        try:
            if coalesce_key is not None:
                now = time.monotonic()
                with self._lock:
                    if len(self._coalesce) >= COALESCE_MAX_ENTRIES:
                        for key in [k for k, (at, _) in self._coalesce.items() if now - at >= COALESCE_SECONDS]:
                            del self._coalesce[key]
                        if len(self._coalesce) >= COALESCE_MAX_ENTRIES:
                            self._coalesce.clear()
                    slot = self._coalesce.setdefault(coalesce_key, [now - COALESCE_SECONDS, 0.0])  # lần đầu: gửi ngay
                    slot[1] += event.get("so_luong") or 1
                    if now - slot[0] < COALESCE_SECONDS:
                        return
                    event = {**event, "so_luong": int(slot[1])}
                    slot[0], slot[1] = now, 0.0
            body = json.dumps(self.build_payload(event, self.now_vn()), ensure_ascii=False, separators=(",", ":")).encode()
            if self._run_inline:
                self._deliver(body, str(event.get("id")))
                return
            with self._lock:
                if self._pending >= MAX_PENDING:
                    return
                self._pending += 1
            self._pool.submit(self._deliver_pooled, body, str(event.get("id")))
        except Exception:
            logger.warning("Không xếp được webhook Major OS", exc_info=True)

    def _deliver_pooled(self, body: bytes, event_id: str) -> None:
        try:
            self._deliver(body, event_id)
        finally:
            with self._lock:
                self._pending -= 1

    def _circuit_open(self) -> bool:
        with self._lock:
            return time.monotonic() < self._paused_until

    def _record(self, ok: bool) -> None:
        with self._lock:
            if ok:
                self._failures = 0
                return
            self._failures += 1
            if self._failures >= BREAKER_FAILURES:
                self._failures = 0
                self._paused_until = time.monotonic() + BREAKER_PAUSE_SECONDS
                logger.warning("Webhook Major OS lỗi liên tiếp %d lần — tạm ngừng gửi %d phút", BREAKER_FAILURES, int(BREAKER_PAUSE_SECONDS // 60))

    def _attempt(self, body: bytes, event_id: str) -> int | None:
        """Gửi 1 lần, trả mã HTTP (None nếu lỗi mạng)."""
        headers = {"Content-Type": "application/json", "X-Signature": sign(self.secret or "", body), "X-Event-Id": event_id}
        try:
            return self._post(self.url, content=body, headers=headers, timeout=TIMEOUT_SECONDS).status_code
        except Exception:
            return None

    def _deliver(self, body: bytes, event_id: str) -> int | None:
        """Gửi có thử lại; trả mã HTTP cuối (None = lỗi mạng hoặc đang ngắt mạch). Không bao giờ ném lỗi."""
        try:
            if self._circuit_open():
                return None
            status = None
            for attempt in range(len(RETRY_DELAYS) + 1):
                status = self._attempt(body, event_id)
                if status is not None and status < 500:
                    break  # 2xx xong; 4xx là lỗi phía mình/phía nhận, thử lại cũng vô ích
                if attempt < len(RETRY_DELAYS):
                    self._sleep(RETRY_DELAYS[attempt])
            self._record(status is not None and (status < 400))
            return status
        except Exception:
            logger.warning("Lỗi gửi webhook Major OS", exc_info=True)
            return None

    def send_test(self) -> int | None:
        """Gửi đồng bộ 1 sự kiện thử `thu_ket_noi` (không qua ngắt mạch/hàng chờ) và trả mã phản hồi."""
        event = {"id": f"test-{int(time.time())}", "tinh_nang": "thu_ket_noi", "thoi_diem": self.now_vn(),
                 "nguoi_dung": None, "so_luong": 1, "chi_tiet": {}}
        body = json.dumps(self.build_payload(event, self.now_vn()), ensure_ascii=False, separators=(",", ":")).encode()
        return self._attempt(body, event["id"])
