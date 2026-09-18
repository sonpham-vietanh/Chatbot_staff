from __future__ import annotations

import io
import json
from pathlib import Path
import re
from typing import Any

import frontmatter

from app.services.admin_service import AdminService

ALLOWED_EXTENSIONS = {".md", ".txt", ".csv", ".json", ".pdf", ".docx"}
MAX_UPLOAD_BYTES = 10 * 1024 * 1024


class KnowledgeIngestService:
    """Trích nội dung văn bản từ file HR/admin upload, tạo thành note draft trong Supabase."""

    def __init__(self, admin_service: AdminService):
        self.admin_service = admin_service

    def ingest(self, filename: str, content: bytes, department: str | None, title: str | None = None) -> dict[str, Any]:
        safe_name = self._safe_filename(filename)
        extension = Path(safe_name).suffix.casefold()
        if extension not in ALLOWED_EXTENSIONS:
            raise ValueError(f"Định dạng chưa được hỗ trợ: {extension or 'không có phần mở rộng'}")
        if len(content) > MAX_UPLOAD_BYTES:
            raise ValueError("File vượt quá giới hạn 10 MB")

        note_title = title.strip() if title and title.strip() else Path(safe_name).stem
        extracted_text = self._extract_text(safe_name, content).strip()
        body = extracted_text or f"File gốc: `{safe_name}`. Chờ HR bổ sung nội dung có thể tìm kiếm."

        note = self.admin_service.create_note(
            title=note_title,
            department=department or "Unassigned",
            content=body,
            status="draft",
            created_by="HR_Upload",
        )
        return {
            "note_id": note["id"],
            "status": "draft",
            "title": note["title"],
            "source_file": safe_name,
            "message": "Đã tạo note draft từ file upload. HR cần duyệt trong /admin trước khi chatbot sử dụng.",
        }

    @staticmethod
    def _safe_filename(filename: str) -> str:
        name = Path(filename or "upload").name
        name = re.sub(r"[^\w.() -]+", "_", name, flags=re.UNICODE).strip(" .")
        return name[:160] or "upload"

    @staticmethod
    def _extract_text(filename: str, content: bytes) -> str:
        extension = Path(filename).suffix.casefold()
        if extension in {".md", ".txt", ".csv", ".json"}:
            text = content.decode("utf-8-sig", errors="replace")
            if extension == ".md":
                try:
                    parsed = frontmatter.loads(text)
                    return parsed.content
                except Exception:
                    return text
            if extension == ".json":
                try:
                    return json.dumps(json.loads(text), ensure_ascii=False, indent=2)
                except json.JSONDecodeError:
                    return text
            return text
        if extension == ".pdf":
            from pypdf import PdfReader

            reader = PdfReader(io.BytesIO(content))
            return "\n\n".join(page.extract_text() or "" for page in reader.pages)
        if extension == ".docx":
            from docx import Document

            document = Document(io.BytesIO(content))
            return "\n".join(paragraph.text for paragraph in document.paragraphs)
        return ""
