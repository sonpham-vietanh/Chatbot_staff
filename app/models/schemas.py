from typing import Any, Literal

from pydantic import BaseModel, Field


class ChatTurn(BaseModel):
    role: Literal["user", "assistant"]
    content: str = Field(max_length=2000)


class ChatRequest(BaseModel):
    question: str = Field(min_length=2, max_length=2000)
    user_department: str | None = None
    department: str | None = None
    history: list[ChatTurn] = Field(default_factory=list, max_length=12)
    thread_id: str | None = None


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
