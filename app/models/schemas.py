import datetime
import uuid
from typing import Annotated, Any, Literal

from pydantic import BaseModel, Field, StringConstraints, field_validator

from app.services.api_key_service import normalize_origin

MAX_TURN_CHARS = 2000
MAX_HISTORY_ITEMS = 6
"""Số tin nhắn history thực sự được dùng làm ngữ cảnh — pipeline dùng chung hằng số này."""


class ChatTurn(BaseModel):
    role: Literal["user", "assistant"]
    content: str = Field(description=f"Dài hơn {MAX_TURN_CHARS} ký tự sẽ tự được cắt bớt, không báo lỗi.")

    @field_validator("content", mode="before")
    @classmethod
    def _truncate_content(cls, value: Any) -> Any:
        """History chỉ là ngữ cảnh cho câu hỏi nối tiếp — câu trả lời cũ quá dài thì cắt
        bớt thay vì trả 422 làm hỏng cả cuộc trò chuyện của client (kể cả bên tích hợp)."""
        return value[:MAX_TURN_CHARS] if isinstance(value, str) else value


class ChatRequest(BaseModel):
    # Cắt khoảng trắng 2 đầu TRƯỚC khi kiểm độ dài: câu hỏi toàn dấu cách không được tới LLM.
    question: Annotated[str, StringConstraints(strip_whitespace=True, min_length=2, max_length=2000)]
    user_department: str | None = None
    department: str | None = None
    history: list[ChatTurn] = Field(
        default_factory=list,
        description=f"Chỉ {MAX_HISTORY_ITEMS} tin nhắn cuối được dùng; phần cũ hơn tự bị bỏ, không báo lỗi.",
    )
    thread_id: str | None = None

    @field_validator("history", mode="before")
    @classmethod
    def _keep_latest_history(cls, value: Any) -> Any:
        """Client gửi cả cuộc trò chuyện dài thì chỉ giữ các tin gần nhất, không từ chối
        request."""
        return value[-MAX_HISTORY_ITEMS:] if isinstance(value, list) else value


class Citation(BaseModel):
    source: str
    heading: str
    version: str


class ChatResponse(BaseModel):
    answer: str
    grounded: bool
    citations: list[Citation] = []
    thread_id: str | None = None


class SignupRequest(BaseModel):
    email: str = Field(min_length=3, max_length=200)
    password: str = Field(min_length=6, max_length=200)
    display_name: str = Field(min_length=1, max_length=100)


class LoginRequest(BaseModel):
    email: str = Field(min_length=3, max_length=200)
    password: str = Field(min_length=1, max_length=200)


class RefreshRequest(BaseModel):
    refresh_token: str = Field(min_length=1)


class AuthResponse(BaseModel):
    access_token: str
    refresh_token: str
    user: dict


class ThreadSummary(BaseModel):
    id: str
    title: str
    updated_at: str


class ThreadMessage(BaseModel):
    id: str
    role: str
    content: str
    created_at: str


class TopSource(BaseModel):
    source: str
    count: int


class TopQuestion(BaseModel):
    question: str
    count: int


class AnalyticsSummary(BaseModel):
    scanned_logs: int
    grounded_count: int
    top_sources: list[TopSource]
    top_questions: list[TopQuestion]


FeedbackReason = Literal["wrong_info", "missing_info", "off_topic", "other"]


class FeedbackCreateRequest(BaseModel):
    question: Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=4000)]
    answer: Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=20000)]
    citations: list[Citation] = Field(default_factory=list, max_length=20)
    reason: FeedbackReason
    note: Annotated[str, StringConstraints(strip_whitespace=True, max_length=2000)] | None = None
    thread_id: uuid.UUID | None = None


class FeedbackStatusRequest(BaseModel):
    status: Literal["open", "resolved", "dismissed"]


class ProfileUpdateRequest(BaseModel):
    phone: Annotated[str, StringConstraints(strip_whitespace=True, max_length=30)] | None = None
    bio: Annotated[str, StringConstraints(strip_whitespace=True, max_length=500)] | None = None


class WigCreateRequest(BaseModel):
    title: Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=200)]
    period_label: Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=40)]
    metric_name: Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=120)]
    unit: Annotated[str, StringConstraints(strip_whitespace=True, max_length=30)] | None = None
    description: Annotated[str, StringConstraints(strip_whitespace=True, max_length=2000)] | None = None
    start_value: float = 0
    target_value: float
    current_value: float | None = None
    due_date: datetime.date | None = None


class WigUpdateRequest(BaseModel):
    title: Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=200)] | None = None
    period_label: Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=40)] | None = None
    metric_name: Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=120)] | None = None
    unit: Annotated[str, StringConstraints(strip_whitespace=True, max_length=30)] | None = None
    description: Annotated[str, StringConstraints(strip_whitespace=True, max_length=2000)] | None = None
    start_value: float | None = None
    target_value: float | None = None
    due_date: datetime.date | None = None
    status: Literal["active", "done", "dropped"] | None = None


class WigProgressRequest(BaseModel):
    value: float
    note: Annotated[str, StringConstraints(strip_whitespace=True, max_length=1000)] | None = None


Department = Literal["HR", "Finance", "Academic", "Admin", "Unassigned"]
NoteStatus = Literal["draft", "approved", "rejected"]
AccessLevel = Literal["staff", "manager", "admin"]


class ManageNoteCreateRequest(BaseModel):
    title: Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=200)]
    department: Department
    content: Annotated[str, StringConstraints(min_length=1, max_length=100_000)]
    status: NoteStatus = "approved"
    access_level: AccessLevel = "staff"


class ManageNoteUpdateRequest(BaseModel):
    title: Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=200)] | None = None
    department: Department | None = None
    content: Annotated[str, StringConstraints(min_length=1, max_length=100_000)] | None = None
    status: NoteStatus | None = None
    access_level: AccessLevel | None = None
    base_updated_at: str | None = Field(default=None, max_length=64)  # updated_at của bản đang sửa; lệch = có người sửa trước


class LeaderUpsertRequest(BaseModel):
    email: Annotated[str, StringConstraints(strip_whitespace=True, to_lower=True, min_length=3, max_length=200)]
    display_name: Annotated[str, StringConstraints(strip_whitespace=True, max_length=100)] = ""
    departments: list[Department] = Field(default_factory=list, max_length=5)
    active: bool = True


class ReportKeyCreateRequest(BaseModel):
    label: Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=100)]


class ApiKeyCreateRequest(BaseModel):
    # Khớp ràng buộc của bảng api_keys (btrim(label) dài 1..100) để nhãn toàn dấu cách bị
    # trả 422 ở đây thay vì thành lỗi của database -> 500.
    label: Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=100)]
    allowed_origin: str = Field(min_length=1, max_length=300)

    @field_validator("allowed_origin")
    @classmethod
    def _normalize_allowed_origin(cls, value: str) -> str:
        origin = normalize_origin(value)
        if not origin:
            raise ValueError("Phải là origin dạng https://ten-mien, không kèm đường dẫn")
        return origin


class ApiKeyOut(BaseModel):
    id: str
    label: str
    key: str
    allowed_origin: str
    status: str
    created_at: str
    last_used_at: str | None = None


class SearchResult(BaseModel):
    id: str
    text: str
    score: float
    metadata: dict[str, Any]


class NoteUpdateRequest(BaseModel):
    title: str = Field(min_length=1, max_length=200)
    department: str
    status: str
    version: str
    access_level: str
    content: str = Field(min_length=1)


class NoteCreateRequest(BaseModel):
    title: str = Field(min_length=1, max_length=200)
    department: str = "Unassigned"
    content: str = Field(min_length=1)
    access_level: str = "staff"
    status: str = "draft"
