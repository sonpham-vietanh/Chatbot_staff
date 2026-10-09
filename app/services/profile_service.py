"""Hồ sơ nhân viên: thông tin HR (chỉ đọc) + phần nhân viên tự cập nhật (điện thoại, giới thiệu ngắn)."""
from __future__ import annotations

from datetime import date, datetime, timezone
from typing import Any

from app.services.supabase_client import SupabaseClient


def tenure_text(start: str | None, today: date | None = None) -> str | None:
    """"2 năm 3 tháng" / "5 tháng" / "dưới 1 tháng" tính từ ngày vào làm; None nếu chưa có ngày."""
    if not start:
        return None
    try:
        began = date.fromisoformat(str(start)[:10])
    except ValueError:
        return None
    today = today or date.today()
    if began > today:
        return None
    months = (today.year - began.year) * 12 + (today.month - began.month) - (1 if today.day < began.day else 0)
    years, rest = divmod(max(months, 0), 12)
    if years and rest:
        return f"{years} năm {rest} tháng"
    if years:
        return f"{years} năm"
    return f"{rest} tháng" if rest else "dưới 1 tháng"


class ProfileService:
    def __init__(self, client: SupabaseClient):
        self.client = client

    def get(self, email: str) -> dict[str, Any]:
        email = email.strip().casefold()
        hr = self.client.select("employee_directory", {
            "select": "email,display_name,department,job_title,employment_start_date,manager_email,campus,work_phone",
            "email": f"eq.{email}", "active": "eq.true", "limit": "1"})
        hr_row = hr[0] if hr else None
        own = self.client.select("staff_profiles", {"select": "phone,bio,updated_at", "email": f"eq.{email}", "limit": "1"})
        manager = None
        if hr_row and hr_row.get("manager_email"):
            rows = self.client.select("employee_directory", {"select": "email,display_name", "email": f"eq.{hr_row['manager_email']}", "limit": "1"})
            manager = rows[0] if rows else {"email": hr_row["manager_email"], "display_name": None}
        return {
            "email": email,
            "in_directory": hr_row is not None,
            "display_name": (hr_row or {}).get("display_name"),
            "department": (hr_row or {}).get("department"),
            "job_title": (hr_row or {}).get("job_title"),
            "campus": (hr_row or {}).get("campus"),
            "work_phone": (hr_row or {}).get("work_phone"),
            "employment_start_date": (hr_row or {}).get("employment_start_date"),
            "tenure": tenure_text((hr_row or {}).get("employment_start_date")),
            "manager": manager,
            "phone": (own[0] if own else {}).get("phone"),
            "bio": (own[0] if own else {}).get("bio"),
            "has_team": bool(self.client.select("employee_directory", {"select": "email", "manager_email": f"eq.{email}", "active": "eq.true", "limit": "1"})),
        }

    def update_self(self, email: str, phone: str | None, bio: str | None) -> None:
        email = email.strip().casefold()
        row = {"email": email, "phone": (phone or "").strip()[:30] or None, "bio": (bio or "").strip()[:500] or None,
               "updated_at": datetime.now(timezone.utc).isoformat()}
        existing = self.client.select("staff_profiles", {"select": "email", "email": f"eq.{email}", "limit": "1"})
        if existing:
            self.client.update("staff_profiles", {"email": f"eq.{email}"}, {k: v for k, v in row.items() if k != "email"})
        else:
            self.client.insert("staff_profiles", [row], returning=False)
