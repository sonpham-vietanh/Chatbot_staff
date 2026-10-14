"""Yêu cầu chỉnh sửa thông tin HR: nhân viên gửi, HR duyệt (áp giá trị vào danh bạ) hoặc từ chối."""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from app.services.directory_service import REQUESTABLE_FIELDS, DirectoryError, DirectoryService, normalize_email, parse_date
from app.services.supabase_client import SupabaseClient

REQUEST_STATUSES = ("pending", "approved", "rejected", "cancelled")
REQUEST_COLUMNS = "id,email,field,old_value,new_value,reason,status,reviewed_by,review_note,created_at,reviewed_at"


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _text(value: Any) -> str:
    return "" if value is None else str(value)


def request_view(row: dict[str, Any]) -> dict[str, Any]:
    field = row.get("field")
    return {**{k: row.get(k) for k in ("id", "field", "old_value", "new_value", "reason", "status", "review_note", "created_at", "reviewed_at")},
            "label": REQUESTABLE_FIELDS.get(field, {}).get("label", field)}


class ChangeRequestService:
    def __init__(self, client: SupabaseClient, directory: DirectoryService):
        self.client = client
        self.directory = directory

    def _get(self, request_id: str) -> dict[str, Any]:
        rows = self.client.select("profile_change_requests", {"select": REQUEST_COLUMNS, "id": f"eq.{request_id}", "limit": "1"})
        if not rows:
            raise DirectoryError(404, "Không tìm thấy yêu cầu.")
        return rows[0]

    # ---------- nhân viên ----------
    def list_for(self, email: str, limit: int = 20) -> list[dict[str, Any]]:
        rows = self.client.select("profile_change_requests", {
            "select": REQUEST_COLUMNS, "email": f"eq.{normalize_email(email)}", "order": "created_at.desc", "limit": str(limit)})
        return [request_view(r) for r in rows]

    def create(self, email: str, field: str, new_value: Any, reason: Any = None) -> dict[str, Any]:
        email = normalize_email(email)
        spec = REQUESTABLE_FIELDS.get(field)
        if spec is None:
            raise DirectoryError(422, "Trường này không thể gửi yêu cầu chỉnh sửa, hãy liên hệ HR.")
        value = " ".join(_text(new_value).split())
        if not value:
            raise DirectoryError(422, f"Giá trị mới của {spec['label']} không được để trống.")
        if len(value) > spec["max_length"]:
            raise DirectoryError(422, f"{spec['label']} dài tối đa {spec['max_length']} ký tự.")
        if spec["type"] == "date":
            value = parse_date(value, spec["label"])
        note = " ".join(_text(reason).split()) or None
        if note and len(note) > 500:
            raise DirectoryError(422, "Lý do dài tối đa 500 ký tự.")
        current = self.directory.get(email)
        if current is None or not current.get("active"):
            raise DirectoryError(404, "Bạn chưa có trong danh bạ nhân sự nên chưa có thông tin để chỉnh sửa. Hãy liên hệ HR.")
        if _text(current.get(field)) == value:
            raise DirectoryError(422, "Giá trị mới giống với giá trị hiện tại.")
        pending = self.client.select("profile_change_requests", {"select": "id", "email": f"eq.{email}", "field": f"eq.{field}", "status": "eq.pending", "limit": "1"})
        if pending:
            raise DirectoryError(409, f"Bạn đã có một yêu cầu đang chờ duyệt cho {spec['label']}. Hãy huỷ yêu cầu cũ trước khi gửi lại.")
        try:
            rows = self.client.insert("profile_change_requests", [{
                "email": email, "field": field, "old_value": current.get(field), "new_value": value, "reason": note, "status": "pending"}])
        except Exception as error:  # chỉ mục unique (email, field) khi hai yêu cầu đến cùng lúc
            if "duplicate" in str(error).lower() or "23505" in str(error) or "409" in str(error):
                raise DirectoryError(409, f"Bạn đã có một yêu cầu đang chờ duyệt cho {spec['label']}.") from error
            raise
        return request_view(rows[0])

    def cancel(self, email: str, request_id: str) -> dict[str, str]:
        row = self._get(request_id)
        if normalize_email(row["email"]) != normalize_email(email):
            raise DirectoryError(403, "Bạn không có quyền huỷ yêu cầu này.")
        if row["status"] != "pending":
            raise DirectoryError(409, "Yêu cầu này không còn ở trạng thái chờ duyệt.")
        self.client.update("profile_change_requests", {"id": f"eq.{request_id}", "status": "eq.pending"}, {"status": "cancelled", "reviewed_at": _now()})
        return {"status": "cancelled"}

    # ---------- admin ----------
    def admin_list(self, status: str = "pending", limit: int = 300) -> dict[str, Any]:
        params = {"select": REQUEST_COLUMNS, "order": "created_at.desc", "limit": str(limit)}
        if status != "all":
            params["status"] = f"eq.{status}"
        rows = self.client.select("profile_change_requests", params)
        emails = sorted({r["email"] for r in rows})
        people: dict[str, dict[str, Any]] = {}
        if emails:
            found = self.client.select("employee_directory", {"select": "email,display_name,department", "email": "in.(" + ",".join(emails) + ")", "limit": str(len(emails))})
            people = {p["email"]: p for p in found}
        items = [{**request_view(r), "email": r["email"], "display_name": people.get(r["email"], {}).get("display_name"),
                  "department": people.get(r["email"], {}).get("department")} for r in rows]
        pending_count = len(self.client.select("profile_change_requests", {"select": "id", "status": "eq.pending"}))
        return {"items": items, "pending_count": pending_count}

    def approve(self, request_id: str, note: str | None = None) -> dict[str, Any]:
        row = self._get(request_id)
        if row["status"] != "pending":
            raise DirectoryError(409, "Yêu cầu này không còn ở trạng thái chờ duyệt.")
        field, email = row["field"], row["email"]
        if field not in REQUESTABLE_FIELDS:
            raise DirectoryError(422, "Trường của yêu cầu này không còn được phép chỉnh sửa.")
        current = self.directory.get(email)
        if current is None:
            raise DirectoryError(404, "Nhân viên này không còn trong danh bạ.")
        if _text(current.get(field)) != _text(row.get("old_value")):
            raise DirectoryError(409, "Dữ liệu đã đổi từ lúc gửi yêu cầu, hãy từ chối hoặc nhờ nhân viên gửi lại.")
        new_value = row["new_value"]
        self.client.update("employee_directory", {"email": f"eq.{email}"}, {field: new_value, "updated_at": _now()})
        updated = self.client.update("profile_change_requests", {"id": f"eq.{request_id}", "status": "eq.pending"}, {
            "status": "approved", "reviewed_by": "admin", "review_note": (note or "").strip()[:500] or None, "reviewed_at": _now()})
        self.directory.audit(email, "admin", "request_approved", {field: {"old": row.get("old_value"), "new": new_value}})
        return request_view(updated[0]) if updated else request_view({**row, "status": "approved"})

    def reject(self, request_id: str, note: str | None) -> dict[str, Any]:
        reason = " ".join(_text(note).split())
        if not reason:
            raise DirectoryError(422, "Cần ghi lý do từ chối.")
        if len(reason) > 500:
            raise DirectoryError(422, "Lý do dài tối đa 500 ký tự.")
        row = self._get(request_id)
        if row["status"] != "pending":
            raise DirectoryError(409, "Yêu cầu này không còn ở trạng thái chờ duyệt.")
        updated = self.client.update("profile_change_requests", {"id": f"eq.{request_id}", "status": "eq.pending"}, {
            "status": "rejected", "reviewed_by": "admin", "review_note": reason, "reviewed_at": _now()})
        self.directory.audit(row["email"], "admin", "request_rejected", {row["field"]: {"old": row.get("old_value"), "new": row["new_value"], "note": reason}})
        return request_view(updated[0]) if updated else request_view({**row, "status": "rejected"})
