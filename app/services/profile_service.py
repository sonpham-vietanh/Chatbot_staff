"""Hồ sơ nhân viên: thông tin HR (chỉ đọc) + phần nhân viên tự cập nhật (liên hệ, ngày sinh, học vấn, liên hệ khẩn cấp...)."""
from __future__ import annotations

import logging
from datetime import date, datetime, timezone
from typing import Any

from app.services.directory_service import EMPLOYMENT_TYPES, REQUESTABLE_FIELDS
from app.services.supabase_client import SupabaseClient

logger = logging.getLogger(__name__)
# Trường nhân viên tự cập nhật (bảng staff_profiles) -> độ dài tối đa. Chỉ chính chủ và admin xem.
SELF_FIELDS = {"phone": 30, "zalo": 40, "address": 300, "education": 200, "expertise": 500, "bio": 500,
               "emergency_name": 100, "emergency_relation": 60, "emergency_phone": 30}
GENDERS = ("nam", "nu", "khac")
HR_COLUMNS = "email,display_name,department,job_title,employment_start_date,manager_email,campus,work_phone,employee_code,level_name,contract_type,employment_type,team_name,extra_roles,subjects,grade_levels"
SELF_COLUMNS = "birth_date,gender,updated_at,consent_at," + ",".join(SELF_FIELDS)


class ProfileError(Exception):
    def __init__(self, detail: str):
        super().__init__(detail)
        self.status_code = 422
        self.detail = detail


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
            "select": HR_COLUMNS, "email": f"eq.{email}", "active": "eq.true", "limit": "1"})
        hr_row = hr[0] if hr else None
        own = (self.client.select("staff_profiles", {"select": SELF_COLUMNS, "email": f"eq.{email}", "limit": "1"}) or [{}])[0]
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
            "employee_code": (hr_row or {}).get("employee_code"),
            "level_name": (hr_row or {}).get("level_name"),
            "contract_type": (hr_row or {}).get("contract_type"),
            "employment_type": (hr_row or {}).get("employment_type"),
            "employment_type_label": EMPLOYMENT_TYPES.get((hr_row or {}).get("employment_type") or ""),
            "team_name": (hr_row or {}).get("team_name"),
            "extra_roles": (hr_row or {}).get("extra_roles"),
            "subjects": (hr_row or {}).get("subjects"),
            "grade_levels": (hr_row or {}).get("grade_levels"),
            "consent_at": own.get("consent_at"),
            "requestable_fields": [{"field": key, "label": spec["label"], "max_length": spec["max_length"], "type": spec["type"]}
                                   for key, spec in REQUESTABLE_FIELDS.items()],
            "requests": self._requests(email),
            "employment_start_date": (hr_row or {}).get("employment_start_date"),
            "tenure": tenure_text((hr_row or {}).get("employment_start_date")),
            "manager": manager,
            **{key: own.get(key) for key in (*SELF_FIELDS, "birth_date", "gender")},
            "has_team": bool(self.client.select("employee_directory", {"select": "email", "manager_email": f"eq.{email}", "active": "eq.true", "limit": "1"})),
        }

    def _requests(self, email: str) -> list[dict[str, Any]]:
        """20 yêu cầu chỉnh sửa gần nhất của chính người này. Lỗi (vd. chưa chạy migration) không làm hỏng cả hồ sơ."""
        try:
            rows = self.client.select("profile_change_requests", {
                "select": "id,field,old_value,new_value,reason,status,review_note,created_at,reviewed_at",
                "email": f"eq.{email}", "order": "created_at.desc", "limit": "20"})
        except Exception:
            logger.warning("Không đọc được profile_change_requests", exc_info=True)
            return []
        return [{**row, "label": REQUESTABLE_FIELDS.get(row.get("field"), {}).get("label", row.get("field"))} for row in rows]

    def give_consent(self, email: str) -> str:
        """Ghi mốc nhân viên xác nhận đã đọc thông báo mục đích dữ liệu; gọi lại thì giữ mốc đầu tiên."""
        email = email.strip().casefold()
        existing = self.client.select("staff_profiles", {"select": "email,consent_at", "email": f"eq.{email}", "limit": "1"})
        if existing and existing[0].get("consent_at"):
            return existing[0]["consent_at"]
        now = datetime.now(timezone.utc).isoformat()
        if existing:
            self.client.update("staff_profiles", {"email": f"eq.{email}"}, {"consent_at": now})
        else:
            self.client.insert("staff_profiles", [{"email": email, "consent_at": now}], returning=False)
        return now

    def update_self(self, email: str, fields: dict[str, Any]) -> None:
        """Cập nhật các trường nhân viên tự nhập. Chỉ ghi những khoá có trong `fields` (khoá vắng mặt giữ nguyên); chuỗi rỗng = xoá."""
        email = email.strip().casefold()
        patch: dict[str, Any] = {}
        for key, limit in SELF_FIELDS.items():
            if key in fields:
                value = str(fields[key] or "").strip()
                if len(value) > limit:
                    raise ProfileError(f"Trường {key} dài tối đa {limit} ký tự.")
                patch[key] = value or None
        if "gender" in fields:
            gender = (fields["gender"] or "").strip().casefold() or None
            if gender is not None and gender not in GENDERS:
                raise ProfileError("Giới tính không hợp lệ.")
            patch["gender"] = gender
        if "birth_date" in fields:
            raw = str(fields["birth_date"] or "").strip()
            if raw:
                try:
                    born = date.fromisoformat(raw[:10])
                except ValueError as error:
                    raise ProfileError("Ngày sinh không hợp lệ.") from error
                if born > date.today() or born.year < 1920:
                    raise ProfileError("Ngày sinh không hợp lệ.")
            patch["birth_date"] = raw[:10] or None
        patch["updated_at"] = datetime.now(timezone.utc).isoformat()
        existing = self.client.select("staff_profiles", {"select": "email," + SELF_COLUMNS, "email": f"eq.{email}", "limit": "1"})
        if existing:
            self.client.update("staff_profiles", {"email": f"eq.{email}"}, patch)
        else:
            self.client.insert("staff_profiles", [{"email": email, **patch}], returning=False)
        before = existing[0] if existing else {}
        changed = [key for key, value in patch.items() if key != "updated_at" and str(before.get(key) or "") != str(value or "")]
        if changed:  # nhật ký chỉ ghi TÊN trường, không ghi giá trị (thông tin riêng tư)
            try:
                self.client.insert("profile_audit_log", [{"email": email, "actor": email, "action": "self_update", "changes": {"fields": changed}}], returning=False)
            except Exception:
                logger.warning("Không ghi được profile_audit_log", exc_info=True)
