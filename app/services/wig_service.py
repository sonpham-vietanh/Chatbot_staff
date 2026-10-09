"""WIG (mục tiêu trọng yếu) của nhân viên: mỗi người tự nhập, chỉ số đo tự do.

Quyền xem: chính chủ, quản lý trực tiếp (employee_directory.manager_email) và admin. Chỉ chính chủ được sửa/cập nhật tiến độ.
Dữ liệu này KHÔNG đi vào chỉ mục tìm kiếm (RAG); chatbot chỉ đọc đúng WIG của người đang đăng nhập."""
from __future__ import annotations

from datetime import date, datetime, timedelta, timezone
from decimal import Decimal, InvalidOperation
from typing import Any

from app.services.supabase_client import SupabaseClient

WIG_STATUSES = ("active", "done", "dropped")
MAX_ACTIVE_WIGS = 10
VN_TZ = timezone(timedelta(hours=7))


class WigError(Exception):
    def __init__(self, status_code: int, detail: str):
        super().__init__(detail)
        self.status_code = status_code
        self.detail = detail


def to_number(value: Any, label: str) -> float:
    try:
        number = float(Decimal(str(value)))
    except (InvalidOperation, ValueError, TypeError) as error:
        raise WigError(422, f"{label} phải là một số.") from error
    if number != number or number in (float("inf"), float("-inf")) or abs(number) > 1e12:
        raise WigError(422, f"{label} không hợp lệ.")
    return number


def progress_percent(start: float, target: float, current: float) -> float:
    """% đã đạt (0–100). Dùng được cho cả chỉ số "càng cao càng tốt" (mục tiêu > đầu kỳ) lẫn "càng thấp càng tốt"."""
    start, target, current = float(start), float(target), float(current)
    if target == start:
        return 100.0 if current == target else 0.0
    return round(max(0.0, min(100.0, (current - start) / (target - start) * 100)), 1)


def format_number(value: Any) -> str:
    """Số theo kiểu Việt Nam: dấu chấm ngăn nghìn, dấu phẩy thập phân (1.234,5)."""
    number = float(value)
    text = f"{int(number):,}" if number == int(number) else f"{number:,.2f}".rstrip("0").rstrip(".")
    return text.replace(",", "\0").replace(".", ",").replace("\0", ".")


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _clean(text: Any, limit: int) -> str | None:
    value = " ".join(str(text or "").split()) if limit <= 200 else str(text or "").strip()
    return value[:limit] or None


class WigService:
    def __init__(self, client: SupabaseClient, admin_emails: str | None = ""):
        self.client = client
        self.admin_emails = {e.strip().casefold() for e in (admin_emails or "").split(",") if e.strip()}

    # ---------- quyền ----------
    def is_admin(self, email: str | None) -> bool:
        return (email or "").strip().casefold() in self.admin_emails

    def reports_of(self, manager_email: str) -> list[dict[str, Any]]:
        """Nhân viên đang báo cáo trực tiếp cho `manager_email` (theo bảng HR)."""
        return self.client.select("employee_directory", {
            "select": "email,display_name,department,job_title",
            "manager_email": f"eq.{manager_email.strip().casefold()}", "active": "eq.true", "order": "display_name.asc"})

    def can_view(self, viewer: str, owner: str) -> bool:
        viewer, owner = viewer.strip().casefold(), owner.strip().casefold()
        if viewer == owner or self.is_admin(viewer):
            return True
        return any(row["email"] == owner for row in self.reports_of(viewer))

    # ---------- đọc ----------
    @staticmethod
    def decorate(wig: dict[str, Any]) -> dict[str, Any]:
        out = dict(wig)
        out["progress"] = progress_percent(wig["start_value"], wig["target_value"], wig["current_value"])
        due = wig.get("due_date")
        out["overdue"] = bool(due and wig.get("status") == "active" and str(due) < date.today().isoformat() and out["progress"] < 100)
        return out

    def list_for(self, owner: str, include_dropped: bool = False) -> list[dict[str, Any]]:
        params = {"select": "*", "owner_email": f"eq.{owner.strip().casefold()}", "order": "created_at.desc", "limit": "200"}
        rows = self.client.select("wigs", params)
        return [self.decorate(r) for r in rows if include_dropped or r.get("status") != "dropped"]

    def get(self, wig_id: str) -> dict[str, Any]:
        rows = self.client.select("wigs", {"select": "*", "id": f"eq.{wig_id}", "limit": "1"})
        if not rows:
            raise WigError(404, "Không tìm thấy WIG.")
        return rows[0]

    def updates(self, viewer: str, wig_id: str, limit: int = 100) -> list[dict[str, Any]]:
        wig = self.get(wig_id)
        if not self.can_view(viewer, wig["owner_email"]):
            raise WigError(403, "Bạn không có quyền xem WIG này.")
        return self.client.select("wig_updates", {
            "select": "id,value,note,created_by,created_at", "wig_id": f"eq.{wig_id}", "order": "created_at.desc", "limit": str(limit)})

    def team(self, manager: str) -> list[dict[str, Any]]:
        """WIG đang chạy của từng người báo cáo trực tiếp."""
        out = []
        for person in self.reports_of(manager):
            wigs = [w for w in self.list_for(person["email"]) if w["status"] == "active"]
            out.append({**person, "wigs": wigs})
        return out

    # ---------- ghi (chỉ chính chủ) ----------
    def _own(self, owner: str, wig_id: str) -> dict[str, Any]:
        wig = self.get(wig_id)
        if wig["owner_email"] != owner.strip().casefold():
            raise WigError(403, "Chỉ chủ của WIG mới được sửa hoặc cập nhật tiến độ.")
        return wig

    def create(self, owner: str, payload: dict[str, Any]) -> dict[str, Any]:
        owner = owner.strip().casefold()
        title, metric = _clean(payload.get("title"), 200), _clean(payload.get("metric_name"), 120)
        period = _clean(payload.get("period_label"), 40)
        if not title or not metric or not period:
            raise WigError(422, "Cần nhập tên WIG, tên chỉ số đo và kỳ (ví dụ Q4/2026).")
        start = to_number(payload.get("start_value", 0), "Giá trị đầu kỳ")
        target = to_number(payload.get("target_value"), "Mục tiêu")
        if len([w for w in self.list_for(owner) if w["status"] == "active"]) >= MAX_ACTIVE_WIGS:
            raise WigError(409, f"Đang có {MAX_ACTIVE_WIGS} WIG chạy. Hãy hoàn thành hoặc bỏ bớt trước khi thêm.")
        current = to_number(payload["current_value"], "Giá trị hiện tại") if payload.get("current_value") not in (None, "") else start
        row = {"owner_email": owner, "period_label": period, "title": title, "description": _clean(payload.get("description"), 2000),
               "metric_name": metric, "unit": _clean(payload.get("unit"), 30), "start_value": start, "target_value": target,
               "current_value": current, "due_date": payload.get("due_date") or None, "status": "active"}
        created = self.client.insert("wigs", [row])[0]
        self.client.insert("wig_updates", [{"wig_id": created["id"], "value": current, "note": "Tạo WIG", "created_by": owner}], returning=False)
        return self.decorate(created)

    def update(self, owner: str, wig_id: str, patch: dict[str, Any]) -> dict[str, Any]:
        self._own(owner, wig_id)
        allowed: dict[str, Any] = {}
        for key, limit in (("title", 200), ("metric_name", 120), ("period_label", 40), ("description", 2000), ("unit", 30)):
            if key in patch and patch[key] is not None:
                value = _clean(patch[key], limit)
                if key in ("title", "metric_name", "period_label") and not value:
                    raise WigError(422, "Không được để trống tên WIG, chỉ số đo hoặc kỳ.")
                allowed[key] = value
        for key, label in (("start_value", "Giá trị đầu kỳ"), ("target_value", "Mục tiêu")):
            if patch.get(key) is not None:
                allowed[key] = to_number(patch[key], label)
        if "due_date" in patch:
            allowed["due_date"] = patch["due_date"] or None
        if patch.get("status") is not None:
            if patch["status"] not in WIG_STATUSES:
                raise WigError(422, "Trạng thái không hợp lệ.")
            allowed["status"] = patch["status"]
        if not allowed:
            raise WigError(422, "Không có gì để cập nhật.")
        allowed["updated_at"] = _now()
        rows = self.client.update("wigs", {"id": f"eq.{wig_id}"}, allowed)
        return self.decorate(rows[0])

    def add_progress(self, owner: str, wig_id: str, value: Any, note: str | None = None) -> dict[str, Any]:
        wig = self._own(owner, wig_id)
        if wig["status"] != "active":
            raise WigError(409, "WIG này đã đóng. Mở lại (chuyển về đang chạy) trước khi cập nhật tiến độ.")
        number = to_number(value, "Giá trị hiện tại")
        self.client.insert("wig_updates", [{"wig_id": wig_id, "value": number, "note": _clean(note, 1000),
                                            "created_by": owner.strip().casefold()}], returning=False)
        rows = self.client.update("wigs", {"id": f"eq.{wig_id}"}, {"current_value": number, "updated_at": _now()})
        return self.decorate(rows[0])

    # ---------- chatbot ----------
    def chat_summary(self, owner: str, display_name: str | None = None) -> str:
        """Câu trả lời cho "WIG của tôi tới đâu rồi?" — lấy thẳng từ cơ sở dữ liệu, không qua mô hình ngôn ngữ."""
        wigs = [w for w in self.list_for(owner) if w["status"] == "active"]
        if not wigs:
            return ("Bạn chưa có WIG nào đang chạy. Bạn có thể thêm ở trang **Hồ sơ** (menu bên trái, mục Hồ sơ & WIG), "
                    "rồi cập nhật tiến độ ở đó để mình báo lại cho bạn khi cần.")
        lines = [f"Bạn đang có **{len(wigs)} WIG** đang chạy:", ""]
        for wig in wigs:
            unit = f" {wig['unit']}" if wig.get("unit") else ""
            line = (f"- **{wig['title']}** ({wig['period_label']}): **{format_number(wig['progress'])}%** — "
                    f"{wig['metric_name']}: {format_number(wig['current_value'])}/{format_number(wig['target_value'])}{unit}")
            if wig.get("due_date"):
                line += f", hạn {wig['due_date']}"
            if wig.get("overdue"):
                line += " (**đã quá hạn**)"
            lines.append(line)
        lines += ["", "Cập nhật tiến độ ở trang **Hồ sơ**."]
        return "\n".join(lines)
