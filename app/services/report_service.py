"""Key chỉ-đọc cho Major OS + các báo cáo ngoài nhật ký sử dụng (góp ý "Báo sai", cảnh báo nghiệp vụ)."""
from __future__ import annotations

import hashlib
import secrets
import threading
import time
from collections import defaultdict, deque
from datetime import datetime, timedelta, timezone
from typing import Any

from app.services.supabase_client import SupabaseClient
from app.services.usage_service import keyset_page, to_vn

KEY_PREFIX = "vr_"
RATE_LIMIT_PER_MINUTE = 120
FAILED_AUTH_PER_MINUTE = 30
TOUCH_INTERVAL = timedelta(minutes=5)  # không ghi last_used_at cho từng lần gọi — Major OS gọi dày

FEEDBACK_STATUS = {"open": "chua_xu_ly", "resolved": "da_sua", "dismissed": "bo_qua"}
FEEDBACK_REASON = {
    "wrong_info": "thong_tin_sai",
    "missing_info": "thieu_thong_tin",
    "off_topic": "lac_de",
    "other": "khac",
}
STALE_FEEDBACK_AFTER = timedelta(days=3)
LOW_ANSWER_RATE = 0.5
LOW_ANSWER_MIN_QUESTIONS = 30


def hash_key(raw: str) -> str:
    return hashlib.sha256(raw.encode()).hexdigest()


class RateLimiter:
    """Cửa sổ trượt 60 giây theo từng key, trong bộ nhớ từng worker — đủ để chặn vòng lặp lỗi bên gọi."""

    def __init__(self, limit: int = RATE_LIMIT_PER_MINUTE):
        self.limit = limit
        self._hits: dict[str, deque[float]] = defaultdict(deque)
        self._lock = threading.Lock()

    def blocked(self, key_id: str) -> float:
        """Như check() nhưng KHÔNG ghi thêm lượt — dùng để xem một bên (vd. IP gọi sai key) đã bị chặn chưa."""
        now = time.monotonic()
        with self._lock:
            hits = self._hits[key_id]
            while hits and now - hits[0] > 60:
                hits.popleft()
            return max(1.0, 60 - (now - hits[0])) if len(hits) >= self.limit else 0.0

    def check(self, key_id: str) -> float:
        """Trả 0 nếu được phép; nếu không, trả số giây phải đợi."""
        now = time.monotonic()
        with self._lock:
            hits = self._hits[key_id]
            while hits and now - hits[0] > 60:
                hits.popleft()
            if len(hits) >= self.limit:
                return max(1.0, 60 - (now - hits[0]))
            hits.append(now)
            return 0.0


class ReportKeyService:
    def __init__(self, client: SupabaseClient):
        self.client = client
        self.limiter = RateLimiter()
        self.failures = RateLimiter(limit=FAILED_AUTH_PER_MINUTE)  # chống dò key: theo IP gọi sai

    def create(self, label: str) -> dict[str, Any]:
        """Trả key gốc ĐÚNG MỘT LẦN — DB chỉ giữ bản băm."""
        raw = KEY_PREFIX + secrets.token_urlsafe(32)
        row = self.client.insert("report_keys", [{
            "label": label.strip(), "key_hash": hash_key(raw), "key_hint": raw[-4:],
        }])[0]
        return {**self._public(row), "key": raw}

    @staticmethod
    def _public(row: dict[str, Any]) -> dict[str, Any]:
        return {k: row.get(k) for k in ("id", "label", "key_hint", "status", "created_at", "last_used_at", "revoked_at")}

    def list(self) -> list[dict[str, Any]]:
        rows = self.client.select("report_keys", {"select": "id,label,key_hint,status,created_at,last_used_at,revoked_at",
                                                  "order": "created_at.desc"})
        return rows

    def revoke(self, key_id: str) -> bool:
        rows = self.client.update("report_keys", {"id": f"eq.{key_id}", "status": "eq.active"},
                                  {"status": "revoked", "revoked_at": datetime.now(timezone.utc).isoformat()})
        return bool(rows)

    def delete(self, key_id: str) -> None:
        self.client.delete("report_keys", {"id": f"eq.{key_id}"})

    def authenticate(self, raw: str) -> dict[str, Any] | None:
        rows = self.client.select("report_keys", {
            "select": "id,label,status,last_used_at", "key_hash": f"eq.{hash_key(raw)}", "status": "eq.active", "limit": "1"})
        return rows[0] if rows else None

    def touch(self, record: dict[str, Any]) -> None:
        last = record.get("last_used_at")
        if last:
            try:
                if datetime.now(timezone.utc) - datetime.fromisoformat(last.replace("Z", "+00:00")) < TOUCH_INTERVAL:
                    return
            except ValueError:
                pass
        try:
            self.client.update("report_keys", {"id": f"eq.{record['id']}"}, {"last_used_at": datetime.now(timezone.utc).isoformat()})
        except Exception:
            pass


class ReportService:
    def __init__(self, client: SupabaseClient):
        self.client = client

    def feedback_page(self, *, tu, den, cursor, limit) -> tuple[list[dict[str, Any]], str | None]:
        rows, next_cursor = keyset_page(
            self.client, "answer_feedback", "created_at",
            "id,created_at,user_email,reason,note,status,handled_at,question", tu=tu, den=den, cursor=cursor, limit=limit)
        names = self._names_by_email({(r.get("user_email") or "").casefold() for r in rows} - {""})
        return [{
            "id": row["id"],
            "luc": to_vn(row["created_at"]),
            "nguoi": ({"email": (row["user_email"]).casefold(), "ten": names.get(row["user_email"].casefold())} if row.get("user_email") else None),
            "ly_do": FEEDBACK_REASON.get(row["reason"], row["reason"]),
            "ghi_chu": row.get("note"),
            "cau_hoi": (row.get("question") or "")[:300],
            "trang_thai": FEEDBACK_STATUS.get(row["status"], row["status"]),
            "xu_ly_luc": to_vn(row.get("handled_at")),
        } for row in rows], next_cursor

    def _names_by_email(self, emails: set[str]) -> dict[str, str]:
        """Họ tên nhân viên lấy từ nhật ký sử dụng (báo sai chỉ lưu email). Lỗi tra cứu thì để trống tên."""
        if not emails:
            return {}
        try:
            rows = self.client.select("usage_events", {
                "select": "user_email,user_name,occurred_at", "user_email": f"in.({','.join(sorted(emails))})",
                "user_name": "not.is.null", "order": "occurred_at.desc", "limit": "500"})
        except Exception:
            return {}
        names: dict[str, str] = {}
        for row in rows:
            names.setdefault(row["user_email"], row["user_name"])
        return names

    def alerts(self) -> list[dict[str, Any]]:
        """Ảnh chụp trạng thái HIỆN TẠI (không lọc theo thời gian). id = loại cảnh báo nên lấy lại không sinh trùng."""
        now = datetime.now(timezone.utc)
        out: list[dict[str, Any]] = []

        open_feedback = self.client.select("answer_feedback", {"select": "id,created_at", "status": "eq.open", "order": "created_at.asc", "limit": "1000"})
        if open_feedback:
            oldest = datetime.fromisoformat(open_feedback[0]["created_at"].replace("Z", "+00:00"))
            out.append({
                "id": "bao_sai_chua_xu_ly", "loai": "bao_sai_chua_xu_ly",
                "muc": "canh_bao" if now - oldest > STALE_FEEDBACK_AFTER else "thong_tin",
                "tieu_de": f"{len(open_feedback)} báo cáo câu trả lời sai chưa xử lý",
                "mo_ta": "Nhân viên báo trợ lý trả lời sai/thiếu; admin cần xem và sửa tài liệu.",
                "so_luong": len(open_feedback), "tu_luc": to_vn(open_feedback[0]["created_at"]), "duong_dan": "/admin",
            })

        drafts = self.client.select("knowledge_notes", {"select": "id,title,created_by,created_at", "status": "eq.draft", "order": "created_at.asc", "limit": "1000"})
        gaps = [d for d in drafts if d.get("created_by") == "AI_Bot" and str(d.get("title", "")).startswith("[CẦN BỔ SUNG]")]
        gap_ids = {d["id"] for d in gaps}
        pending = [d for d in drafts if d["id"] not in gap_ids]
        if gaps:
            out.append({
                "id": "cau_hoi_chua_tra_loi", "loai": "cau_hoi_chua_tra_loi", "muc": "thong_tin",
                "tieu_de": f"{len(gaps)} câu hỏi trợ lý chưa trả lời được",
                "mo_ta": "Câu hỏi của nhân viên mà tài liệu hiện có chưa đủ để trả lời; cần bổ sung tài liệu.",
                "so_luong": len(gaps), "tu_luc": to_vn(gaps[0]["created_at"]), "duong_dan": "/admin",
            })
        if pending:
            out.append({
                "id": "tai_lieu_cho_duyet", "loai": "tai_lieu_cho_duyet", "muc": "thong_tin",
                "tieu_de": f"{len(pending)} tài liệu đang chờ duyệt",
                "mo_ta": "Tài liệu ở trạng thái nháp; trợ lý chưa dùng cho tới khi được duyệt.",
                "so_luong": len(pending), "tu_luc": to_vn(pending[0]["created_at"]), "duong_dan": "/admin",
            })

        since = (now - timedelta(days=7)).isoformat()
        logs = self.client.select("chat_logs", {"select": "grounded", "created_at": f"gte.{since}", "order": "created_at.desc", "limit": "1000"})
        if len(logs) >= LOW_ANSWER_MIN_QUESTIONS:
            rate = sum(1 for row in logs if row.get("grounded")) / len(logs)
            if rate < LOW_ANSWER_RATE:
                out.append({
                    "id": "ty_le_tra_loi_duoc_thap", "loai": "ty_le_tra_loi_duoc_thap", "muc": "canh_bao",
                    "tieu_de": f"Chỉ {round(rate * 100)}% câu hỏi 7 ngày qua trả lời được từ tài liệu",
                    "mo_ta": f"Trong {len(logs)} câu hỏi, tỷ lệ có nguồn tài liệu thấp hơn {round(LOW_ANSWER_RATE * 100)}%; kho tri thức đang thiếu nội dung.",
                    "so_luong": len(logs), "tu_luc": to_vn(since), "duong_dan": "/admin",
                })
        return out
