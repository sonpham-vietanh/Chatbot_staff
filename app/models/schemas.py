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


class Citation(BaseModel):
    source: str
    heading: str
    version: str


class ChatResponse(BaseModel):
    answer: str
    grounded: bool
    citations: list[Citation] = []


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
