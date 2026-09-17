from datetime import datetime, timezone
import json
from pathlib import Path
import re


class DraftService:
    """Tạo draft Obsidian an toàn để HR/admin duyệt hai chiều."""

    def __init__(self, review_path: Path):
        self.review_path = review_path
        self.review_path.mkdir(parents=True, exist_ok=True)

    def create_unanswered_draft(self, question: str, user_department: str | None = None) -> Path:
        timestamp = datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")
        safe_question = re.sub(r"[^a-zA-Z0-9_\-]+", "_", question)[:60].strip("_")
        filename_timestamp = timestamp.replace(":", "").replace("-", "")
        path = self.review_path / f"{filename_timestamp}_{safe_question or 'question'}.md"
        title = f"[CẦN BỔ SUNG] {question}"
        frontmatter = {
            "title": title,
            "department": user_department or "Unassigned",
            "owner": "hr@vietanh.edu.vn",
            "status": "draft",
            "created_by": "AI_Bot",
            "created_at": timestamp,
            "version": "0.1",
            "access_level": "staff",
        }
        yaml_lines = ["---"] + [f"{key}: {json.dumps(str(value), ensure_ascii=False)}" for key, value in frontmatter.items()] + ["---"]
        content = "\n".join(yaml_lines) + f"""

# Câu hỏi chưa có câu trả lời được duyệt

{question}

## Ghi chú cho HR/admin

Bổ sung nội dung, owner, department, version và chuyển `status` thành `approved` sau khi duyệt.
"""
        path.write_text(content, encoding="utf-8")
        return path
