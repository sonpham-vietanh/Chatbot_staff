from __future__ import annotations

from datetime import datetime, timezone
import io
import json
from pathlib import Path
import re
import shutil
from typing import Any
from uuid import uuid4

import frontmatter


ALLOWED_EXTENSIONS = {".md", ".txt", ".csv", ".json", ".pdf", ".docx", ".png", ".jpg", ".jpeg", ".webp"}
MAX_UPLOAD_BYTES = 10 * 1024 * 1024


class KnowledgeIngestService:
    """Ghi dữ liệu do HR upload thành draft note trong Obsidian Vault."""

    def __init__(self, vault_path: Path):
        self.vault_path = vault_path.resolve()
        self.upload_path = self.vault_path / ".staff_uploads"
        self.upload_path.mkdir(parents=True, exist_ok=True)

    def ingest(self, filename: str, content: bytes, department: str | None, title: str | None = None) -> dict[str, Any]:
        safe_name = self._safe_filename(filename)
        extension = Path(safe_name).suffix.casefold()
        if extension not in ALLOWED_EXTENSIONS:
            raise ValueError(f"Định dạng chưa được hỗ trợ: {extension or 'không có phần mở rộng'}")
        if len(content) > MAX_UPLOAD_BYTES:
            raise ValueError("File vượt quá giới hạn 10 MB")

        timestamp = datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")
        slug = re.sub(r"[^a-zA-Z0-9_-]+", "_", Path(safe_name).stem).strip("_") or "uploaded_knowledge"
        identifier = uuid4().hex[:8]
        note_title = title.strip() if title and title.strip() else Path(safe_name).stem
        note_path = self.vault_path / f"[DRAFT] {slug}_{identifier}.md"
        extracted_text = self._extract_text(safe_name, content)
        attachment_path: Path | None = None

        if extension in {".png", ".jpg", ".jpeg", ".webp"}:
            attachment_path = self.upload_path / f"{identifier}_{safe_name}"
            attachment_path.write_bytes(content)
            body = f"![[.staff_uploads/{attachment_path.name}]]\n\nChờ HR bổ sung mô tả hoặc OCR nội dung hình ảnh."
        else:
            body = extracted_text.strip() or f"File gốc: `{safe_name}`. Chờ HR bổ sung nội dung có thể tìm kiếm."

        metadata = {
            "title": f"[CẦN DUYỆT] {note_title}",
            "department": department or "Unassigned",
            "owner": "hr@vietanh.edu.vn",
            "status": "draft",
            "created_by": "HR_Upload",
            "created_at": timestamp,
            "version": "0.1",
            "access_level": "staff",
            "source_file": safe_name,
        }
        note_path.write_text(self._render_note(metadata, body), encoding="utf-8")
        return {
            "note_path": str(note_path),
            "status": "draft",
            "title": metadata["title"],
            "source_file": safe_name,
            "attachment_path": str(attachment_path) if attachment_path else None,
            "message": "Đã đưa dữ liệu vào Obsidian dưới dạng draft. HR cần duyệt status thành approved trước khi chatbot sử dụng.",
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

    @staticmethod
    def _render_note(metadata: dict[str, Any], body: str) -> str:
        lines = ["---"]
        for key, value in metadata.items():
            lines.append(f"{key}: {json.dumps(str(value), ensure_ascii=False)}")
        lines.extend(["---", "", body, ""])
        return "\n".join(lines)
