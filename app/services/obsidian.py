from dataclasses import dataclass
import json
import re
from pathlib import Path
from typing import Any

import frontmatter


@dataclass
class KnowledgeChunk:
    id: str
    text: str
    metadata: dict[str, Any]


class ObsidianLoader:
    def __init__(self, vault_path: Path, graph_path: Path):
        self.vault_path = vault_path
        self.graph_path = graph_path

    def load_approved_chunks(self) -> tuple[list[KnowledgeChunk], int, int]:
        chunks: list[KnowledgeChunk] = []
        graph_edges: list[dict[str, str]] = []
        indexed_files = 0
        for path in sorted(self.vault_path.rglob("*.md")):
            post = frontmatter.load(path)
            metadata = {key: value for key, value in post.metadata.items()}
            if str(metadata.get("status", "")).lower() != "approved":
                continue
            links = self.extract_wikilinks(post.content)
            graph_edges.extend(
                {"source": path.name, "target": target} for target in links
            )
            indexed_files += 1
            chunks.extend(self.chunk_file(path, post.content, metadata))
        self._save_graph(graph_edges)
        return chunks, indexed_files, len(graph_edges)

    def chunk_file(self, path: Path, content: str, metadata: dict[str, Any]) -> list[KnowledgeChunk]:
        chunks: list[KnowledgeChunk] = []
        heading_path: list[str] = []
        buffer: list[str] = []
        chunk_number = 0

        def flush() -> None:
            nonlocal chunk_number
            text = "\n".join(buffer).strip()
            if not text:
                return
            chunk_number += 1
            chunk_metadata = {
                "source": path.name,
                "source_file": path.name,
                "title": str(metadata.get("title", path.stem)),
                "department": str(metadata.get("department", "unknown")),
                "owner": str(metadata.get("owner", "unknown")),
                "status": str(metadata.get("status", "")),
                "version": str(metadata.get("version", "unknown")),
                "effective_date": str(metadata.get("effective_date", "")),
                "access_level": str(metadata.get("access_level", "staff")),
                "heading": " > ".join(heading_path) or "Nội dung chung",
            }
            chunks.append(KnowledgeChunk(
                id=f"{path.stem}:{chunk_number}",
                text=text,
                metadata=chunk_metadata,
            ))
            buffer.clear()

        for line in content.splitlines():
            match = re.match(r"^(#{1,3})\s+(.+?)\s*$", line)
            if match:
                flush()
                level = len(match.group(1))
                heading_path = heading_path[: level - 1]
                heading_path.append(match.group(2))
            buffer.append(line)
        flush()
        return chunks

    @staticmethod
    def extract_wikilinks(content: str) -> list[str]:
        return re.findall(r"\[\[([^\]|#]+)(?:#[^\]|]+)?(?:\|[^\]]+)?\]\]", content)

    def _save_graph(self, edges: list[dict[str, str]]) -> None:
        self.graph_path.parent.mkdir(parents=True, exist_ok=True)
        self.graph_path.write_text(
            json.dumps(edges, ensure_ascii=False, indent=2), encoding="utf-8"
        )
