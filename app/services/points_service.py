"""Sổ điểm đóng góp của nhân viên. Hiện chỉ GHI NHẬN (chưa xếp hạng/đổi thưởng); cộng dồn, không xoá, mỗi sự kiện chỉ tính một lần."""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any

from app.services.supabase_client import SupabaseClient

POINTS = {"bao_sai_gui": 1, "bao_sai_xac_nhan": 3}
REASON_LABELS = {"bao_sai_gui": "Gửi báo sai", "bao_sai_xac_nhan": "Báo sai được xác nhận và đã sửa"}
SUBMIT_DAILY_CAP = 5
"""Tối đa số lần được cộng điểm "gửi báo sai" mỗi ngày, để không ai kiếm điểm bằng cách gửi báo cáo hàng loạt."""
VN_TZ = timezone(timedelta(hours=7))


class PointsService:
    def __init__(self, client: SupabaseClient):
        self.client = client

    def _sent_today(self, email: str) -> int:
        start = datetime.now(VN_TZ).replace(hour=0, minute=0, second=0, microsecond=0).astimezone(timezone.utc).isoformat()
        rows = self.client.select("user_points", {"select": "id", "email": f"eq.{email}", "reason": "eq.bao_sai_gui",
                                                  "created_at": f"gte.{start}", "limit": str(SUBMIT_DAILY_CAP + 1)})
        return len(rows)

    def award(self, email: str | None, reason: str, ref_type: str = "", ref_id: str = "") -> bool:
        """Cộng điểm theo `reason` (xem POINTS). Trả True nếu vừa cộng; False nếu đã cộng cho sự kiện này, hết hạn mức ngày, hoặc lỗi."""
        email = (email or "").strip().casefold()
        if not email or reason not in POINTS:
            return False
        try:
            existing = self.client.select("user_points", {"select": "id", "email": f"eq.{email}", "reason": f"eq.{reason}",
                                                          "ref_type": f"eq.{ref_type}", "ref_id": f"eq.{ref_id}", "limit": "1"})
            if existing:
                return False
            if reason == "bao_sai_gui" and self._sent_today(email) >= SUBMIT_DAILY_CAP:
                return False
            self.client.insert("user_points", [{"email": email, "points": POINTS[reason], "reason": reason,
                                                "ref_type": ref_type, "ref_id": ref_id}], returning=False)
            return True
        except Exception:  # noqa: BLE001 — ghi điểm là việc phụ, không được làm hỏng việc chính
            return False

    def summary(self, email: str, recent: int = 10) -> dict[str, Any]:
        email = email.strip().casefold()
        rows = self.client.select("user_points", {"select": "points,reason,created_at", "email": f"eq.{email}",
                                                  "order": "created_at.desc", "limit": "1000"})
        return {"total": sum(int(r["points"]) for r in rows),
                "recent": [{"points": r["points"], "reason": r["reason"], "label": REASON_LABELS.get(r["reason"], r["reason"]),
                            "created_at": r["created_at"]} for r in rows[:recent]]}
