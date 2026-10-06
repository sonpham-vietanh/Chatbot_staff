"""Nhật ký sử dụng (bảng usage_events) + các truy vấn báo cáo dùng chung cho Major OS.

Quy ước theo tài liệu "Kết nối app với Major OS v2": mỗi dòng có id cố định, lọc theo [tu, den),
phân trang bằng con trỏ, người dùng nhân viên = email @truongvietanh.com + họ tên."""
from __future__ import annotations

import base64
import json
import logging
import re
from collections import Counter
from datetime import datetime, timedelta, timezone
from typing import Any

from app.services.supabase_client import SupabaseClient

logger = logging.getLogger(__name__)

VN_TZ = timezone(timedelta(hours=7))
DEFAULT_PAGE_SIZE = 500
MAX_PAGE_SIZE = 500
"""Tối đa 500 (+1 để biết còn trang sau): Supabase mặc định cắt mỗi truy vấn ở 1000 dòng, vượt là mất dòng mà không báo lỗi."""
SUMMARY_SCAN_LIMIT = 20_000

# Khoá tính năng CỐ ĐỊNH (Major OS đối chiếu theo khoá, không theo tên hiển thị). Thêm tính năng mới thì thêm
# khoá mới, không đổi nghĩa khoá cũ.
FEATURES: dict[str, dict[str, str]] = {
    "hoi_dap": {"ten": "Hỏi trợ lý nội bộ", "mo_ta": "Nhân viên đăng nhập và đặt một câu hỏi cho trợ lý (mỗi câu hỏi = 1 lần)."},
    "hoi_dap_nhung": {"ten": "Hỏi trợ lý qua giao diện nhúng", "mo_ta": "Câu hỏi gửi qua API nhúng (widget) của đối tác; không có thông tin người dùng."},
    "bao_sai": {"ten": "Báo sai câu trả lời", "mo_ta": "Nhân viên bấm \"Báo sai\" dưới một câu trả lời của trợ lý."},
    "dang_nhap_os": {"ten": "Đăng nhập từ Major OS", "mo_ta": "Nhân viên bấm từ Major OS sang trợ lý và được đăng nhập một lần."},
    "tao_tri_thuc": {"ten": "Tạo tài liệu tri thức", "mo_ta": "Leader/admin tạo tài liệu mới trong trang Quản lý tri thức."},
    "sua_tri_thuc": {"ten": "Sửa tài liệu tri thức", "mo_ta": "Leader/admin lưu thay đổi một tài liệu."},
    "xoa_tri_thuc": {"ten": "Xoá tài liệu tri thức", "mo_ta": "Leader/admin xoá một tài liệu (vẫn khôi phục được từ lịch sử)."},
    "khoi_phuc_tri_thuc": {"ten": "Khôi phục tài liệu tri thức", "mo_ta": "Leader/admin khôi phục tài liệu về một phiên bản cũ."},
}


ID_PATTERN = re.compile(r"[A-Za-z0-9_-]{1,80}")
EXPORTED_META_KEYS = ("tra_loi_duoc", "so_nguon", "nguon", "phong_ban", "ly_do", "nguon_du_lieu")
"""Chỉ các khoá chi_tiet này được đưa sang Major OS — không bao giờ đưa tiêu đề/nội dung tài liệu hay hội thoại."""
SUMMARY_DEFAULT_DAYS = 30


class ReportQueryError(ValueError):
    """Tham số báo cáo không hợp lệ (trả về 422)."""


def parse_time(value: str | None, name: str) -> datetime | None:
    """ISO 8601 có múi giờ. Chuỗi không có múi giờ bị từ chối thay vì đoán — tránh lệch 7 tiếng âm thầm."""
    if value is None or value.strip() == "":
        return None
    text = value.strip()
    # "+07:00" viết thẳng trên URL (không mã hoá thành %2B) bị máy chủ giải mã thành " 07:00".
    text = re.sub(r"(?<=\d) (?=\d{2}:?\d{2}$)", "+", text)
    if text[-1:] in ("z", "Z"):
        text = text[:-1] + "+00:00"
    try:
        parsed = datetime.fromisoformat(text)
    except ValueError as error:
        raise ReportQueryError(f"Tham số {name} phải là ISO 8601 có múi giờ, ví dụ 2026-10-04T00:00:00+07:00.") from error
    if parsed.tzinfo is None:
        raise ReportQueryError(f"Tham số {name} thiếu múi giờ (ví dụ +07:00 hoặc Z).")
    return parsed.astimezone(timezone.utc)


def to_vn(value: str | None) -> str | None:
    """Giờ lưu trong DB là UTC; trả ra theo giờ Việt Nam (+07:00) như ví dụ trong tài liệu Major OS."""
    if not value:
        return None
    return datetime.fromisoformat(value.replace("Z", "+00:00")).astimezone(VN_TZ).isoformat()


def encode_cursor(moment: str, row_id: str) -> str:
    raw = json.dumps([moment, row_id], separators=(",", ":")).encode()
    return base64.urlsafe_b64encode(raw).rstrip(b"=").decode()


def decode_cursor(cursor: str) -> tuple[str, str]:
    try:
        moment, row_id = json.loads(base64.urlsafe_b64decode(cursor + "=" * (-len(cursor) % 4)))
        parsed = datetime.fromisoformat(str(moment).replace("Z", "+00:00"))
        if parsed.tzinfo is None or not isinstance(row_id, str) or not ID_PATTERN.fullmatch(row_id):
            raise ValueError
        return parsed.isoformat(), row_id  # chuẩn hoá lại: con trỏ tự dựng không chèn được ký tự lạ vào bộ lọc
    except (ValueError, TypeError, json.JSONDecodeError) as error:
        raise ReportQueryError("Con trỏ trang không hợp lệ.") from error


def clamp_limit(limit: int | None) -> int:
    if limit is None:
        return DEFAULT_PAGE_SIZE
    return max(1, min(int(limit), MAX_PAGE_SIZE))


def keyset_page(client: SupabaseClient, table: str, time_column: str, select: str, *, tu: datetime | None,
                den: datetime | None, cursor: str | None, limit: int, extra: dict[str, str] | None = None
                ) -> tuple[list[dict[str, Any]], str | None]:
    """Một trang kết quả sắp theo (thời gian, id) tăng dần. Con trỏ khoá theo cặp đó nên lấy lại nhiều lần
    không bao giờ trùng hoặc sót dòng dù nhiều dòng cùng một thời điểm."""
    conditions: list[str] = []
    if tu:
        conditions.append(f"{time_column}.gte.{tu.isoformat()}")
    if den:
        conditions.append(f"{time_column}.lt.{den.isoformat()}")
    if cursor:
        moment, row_id = decode_cursor(cursor)
        conditions.append(f"or({time_column}.gt.{moment},and({time_column}.eq.{moment},id.gt.{row_id}))")
    params = {"select": select, "order": f"{time_column}.asc,id.asc", "limit": str(limit + 1), **(extra or {})}
    if conditions:
        params["and"] = "(" + ",".join(conditions) + ")"
    rows = client.select(table, params)
    more = len(rows) > limit
    rows = rows[:limit]
    next_cursor = encode_cursor(rows[-1][time_column], rows[-1]["id"]) if more and rows else None
    return rows, next_cursor


class UsageService:
    def __init__(self, client: SupabaseClient):
        self.client = client

    # ---- ghi ----
    def log(self, feature: str, user: dict[str, Any] | None = None, meta: dict[str, Any] | None = None, count: int = 1) -> None:
        """Ghi 1 sự kiện sử dụng. Best-effort: lỗi ghi nhật ký không bao giờ được làm hỏng việc người dùng đang làm."""
        try:
            profile = (user or {}).get("employee_profile") or {}
            metadata = (user or {}).get("user_metadata") or {}
            name = profile.get("display_name") or metadata.get("display_name") or metadata.get("full_name") or metadata.get("name")
            email = (user or {}).get("email")
            self.client.insert("usage_events", [{
                "user_id": (user or {}).get("id"),
                "user_email": email.strip().casefold() if email else None,
                "user_name": name,
                "feature": feature,
                "quantity": count,
                "meta": meta or {},
            }], returning=False)
        except Exception:
            logger.warning("Không ghi được sự kiện sử dụng %s", feature, exc_info=True)

    # ---- đọc ----
    @staticmethod
    def to_report_row(row: dict[str, Any]) -> dict[str, Any]:
        person = {"email": row["user_email"], "ten": row.get("user_name")} if row.get("user_email") else None
        return {
            "id": row["id"],
            "luc": to_vn(row["occurred_at"]),
            "nguoi": person,
            "tinh_nang": row["feature"],
            "so_lan": row.get("quantity") or 1,
            "chi_tiet": {k: v for k, v in (row.get("meta") or {}).items() if k in EXPORTED_META_KEYS},
        }

    def list_events(self, *, tu: datetime | None, den: datetime | None, cursor: str | None, limit: int,
                    feature: str | None = None) -> tuple[list[dict[str, Any]], str | None]:
        extra = {"feature": f"eq.{feature}"} if feature else None
        rows, next_cursor = keyset_page(self.client, "usage_events", "occurred_at",
                                        "id,occurred_at,user_email,user_name,feature,quantity,meta",
                                        tu=tu, den=den, cursor=cursor, limit=limit, extra=extra)
        return [self.to_report_row(r) for r in rows], next_cursor

    def summary(self, *, tu: datetime | None, den: datetime | None, top: int = 20) -> dict[str, Any]:
        tu = tu or (datetime.now(timezone.utc) - timedelta(days=SUMMARY_DEFAULT_DAYS))  # không có tu thì chỉ 30 ngày gần nhất
        rows: list[dict[str, Any]] = []
        cursor = None
        while len(rows) < SUMMARY_SCAN_LIMIT:
            page, cursor = keyset_page(self.client, "usage_events", "occurred_at", "id,occurred_at,user_email,user_name,feature,quantity,meta",
                                       tu=tu, den=den, cursor=cursor, limit=MAX_PAGE_SIZE)
            rows += page
            if not cursor:
                break
        truncated = bool(cursor)

        by_feature: dict[str, dict[str, Any]] = {}
        by_user: dict[str, dict[str, Any]] = {}
        answered = asked = 0
        for row in rows:
            n = row.get("quantity") or 1
            slot = by_feature.setdefault(row["feature"], {"so_lan": 0, "nguoi": set()})
            slot["so_lan"] += n
            email = row.get("user_email")
            if email:
                slot["nguoi"].add(email)
                person = by_user.setdefault(email, {"email": email, "ten": row.get("user_name"), "so_lan": 0})
                person["so_lan"] += n
                person["ten"] = person["ten"] or row.get("user_name")
            if row["feature"] == "hoi_dap" and (row.get("meta") or {}).get("tra_loi_duoc") is not None:
                asked += n
                answered += n if row["meta"]["tra_loi_duoc"] else 0
        features = [
            {"tinh_nang": key, "ten": FEATURES.get(key, {}).get("ten", key), "so_lan": data["so_lan"], "so_nguoi": len(data["nguoi"])}
            for key, data in by_feature.items()
        ]
        features.sort(key=lambda item: -item["so_lan"])
        # Tính năng chưa có lượt nào vẫn phải hiện (so_lan = 0) để Major OS thấy "tính năng nào ít ai dùng".
        known = {item["tinh_nang"] for item in features}
        features += [{"tinh_nang": key, "ten": info["ten"], "so_lan": 0, "so_nguoi": 0} for key, info in FEATURES.items() if key not in known]
        return {
            "tong_so_lan": sum(item["so_lan"] for item in features),
            "so_nguoi_dung": len({e for data in by_feature.values() for e in data["nguoi"]}),
            "ty_le_tra_loi_duoc": round(answered / asked, 3) if asked else None,
            "theo_tinh_nang": features,
            "nguoi_dung_nhieu_nhat": sorted(by_user.values(), key=lambda item: -item["so_lan"])[:top],
            "bi_cat_bot": truncated,
        }
