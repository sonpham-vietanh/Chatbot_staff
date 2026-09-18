import re

MAX_CHUNK_CHARS = 6000
"""Ngưỡng an toàn để không vượt giới hạn token của embedding provider (vd 8192 token của
text-embedding-3-small) kể cả khi 1 heading gom quá nhiều nội dung bên dưới."""


def chunk_content(content: str) -> list[dict[str, str]]:
    """Cắt nội dung markdown theo heading (# tới ####), trả về list {heading, text}."""
    chunks: list[dict[str, str]] = []
    heading_path: list[str] = []
    buffer: list[str] = []

    def flush() -> None:
        text = "\n".join(buffer).strip()
        buffer.clear()
        if not text:
            return
        heading = " > ".join(heading_path) or "Nội dung chung"
        for part in _split_oversized(text):
            chunks.append({"heading": heading, "text": part})

    for line in content.splitlines():
        match = re.match(r"^(#{1,4})\s+(.+?)\s*$", line)
        if match:
            flush()
            level = len(match.group(1))
            heading_path = heading_path[: level - 1]
            heading_path.append(match.group(2))
        buffer.append(line)
    flush()
    return chunks


def _split_oversized(text: str) -> list[str]:
    if len(text) <= MAX_CHUNK_CHARS:
        return [text]
    parts: list[str] = []
    current: list[str] = []
    current_len = 0
    for paragraph in text.split("\n\n"):
        if current_len + len(paragraph) > MAX_CHUNK_CHARS and current:
            parts.append("\n\n".join(current))
            current, current_len = [], 0
        current.append(paragraph)
        current_len += len(paragraph) + 2
    if current:
        parts.append("\n\n".join(current))
    return parts
