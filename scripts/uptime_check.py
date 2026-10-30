"""Kiểm tra sống/chết của staffbot, chạy định kỳ bằng cron hoặc Task Scheduler (mỗi 5 phút).

Cách dùng:
  cron:            */5 * * * * python3 /duong-dan/scripts/uptime_check.py
  Task Scheduler:  chương trình `python`, đối số `scripts/uptime_check.py`, lặp mỗi 5 phút.
Biến môi trường (tuỳ chọn, KHÔNG nhúng khoá trong tệp):
  UPTIME_URL         mặc định https://staffbot.truongvietanh.com/api/health
  UPTIME_MAX_SECONDS ngưỡng chậm, mặc định 5
  ALERT_WEBHOOK_URL  nếu có: POST JSON {"text": ..., "status": "down"|"recovered"} tới đây
Quy tắc: lỗi (khác 200 hoặc quá chậm) 2 lần liên tiếp mới báo; báo 1 lần cho tới khi hồi phục (có báo hồi phục).
Trạng thái lưu ở tệp tạm uptime_check_state.json. Mã thoát 1 khi đang ở trạng thái báo động."""
import json
import os
import sys
import tempfile
import time
from pathlib import Path

import httpx

URL = os.environ.get("UPTIME_URL", "https://staffbot.truongvietanh.com/api/health")
MAX_SECONDS = float(os.environ.get("UPTIME_MAX_SECONDS", "5"))
STATE_PATH = Path(tempfile.gettempdir()) / "uptime_check_state.json"
FAILS_TO_ALERT = 2


def probe(url: str = URL, max_seconds: float = MAX_SECONDS) -> str | None:
    """Trả None nếu khoẻ, ngược lại là mô tả lỗi tiếng Việt."""
    started = time.monotonic()
    try:
        response = httpx.get(url, timeout=max_seconds + 10)
    except Exception as error:  # noqa: BLE001
        return f"Không kết nối được ({type(error).__name__})"
    elapsed = time.monotonic() - started
    if response.status_code != 200:
        return f"Mã trả về {response.status_code}"
    if elapsed > max_seconds:
        return f"Phản hồi chậm {elapsed:.1f}s (ngưỡng {max_seconds:g}s)"
    return None


def load_state(path: Path = STATE_PATH) -> dict:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:  # noqa: BLE001
        return {"fails": 0, "alerted": False}


def notify(text: str, status: str) -> None:
    print(text)
    webhook = os.environ.get("ALERT_WEBHOOK_URL")
    if not webhook:
        return
    try:
        httpx.post(webhook, json={"text": text, "status": status}, timeout=15)
    except Exception as error:  # noqa: BLE001
        print(f"Không gửi được webhook: {type(error).__name__}")


def run(path: Path = STATE_PATH, error: str | None = ..., notifier=notify) -> int:
    """Một lượt kiểm tra; trả mã thoát. `error` truyền sẵn để test, mặc định tự gọi probe()."""
    if error is ...:
        error = probe()
    state = load_state(path)
    if error is None:
        if state.get("alerted"):
            notifier(f"[staffbot] Đã hoạt động lại: {URL}", "recovered")
        state = {"fails": 0, "alerted": False}
    else:
        state["fails"] = state.get("fails", 0) + 1
        if state["fails"] >= FAILS_TO_ALERT and not state.get("alerted"):
            notifier(f"[staffbot] CẢNH BÁO: {URL} lỗi {state['fails']} lần liên tiếp. {error}", "down")
            state["alerted"] = True
    try:
        path.write_text(json.dumps(state), encoding="utf-8")
    except OSError:
        pass
    return 1 if state.get("alerted") else 0


if __name__ == "__main__":
    sys.exit(run())
