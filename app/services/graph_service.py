import json
from pathlib import Path
from typing import Any

from app.services.vault_service import VaultService


class GraphService:
    """Tra cứu cạnh wikilink và mở rộng context từ các note liên quan."""

    def __init__(self, graph_path: Path, vault_service: VaultService):
        self.graph_path = graph_path
        self.vault_service = vault_service

    def linked_files(self, source_files: set[str]) -> set[str]:
        edges = self._load_edges()
        normalized_sources = {self._normalize(item) for item in source_files}
        linked: set[str] = set()
        for edge in edges:
            source = self._normalize(edge.get("source", ""))
            target = self._normalize(edge.get("target", ""))
            if source in normalized_sources:
                linked.add(target)
            if target in normalized_sources:
                linked.add(source)
        linked.difference_update(normalized_sources)
        return linked

    def expand_context(
        self,
        seed_results: list[dict[str, Any]],
        filters: dict[str, Any],
        max_files: int = 4,
        max_chunks: int = 8,
        max_chars: int = 12000,
    ) -> list[dict[str, Any]]:
        """Giữ seed trước, thêm note liên kết, dedup theo chunk id và giới hạn budget."""
        if not seed_results:
            return []
        seed_files = {
            item["metadata"].get("source_file", item["metadata"].get("source", ""))
            for item in seed_results
        }
        expanded: list[dict[str, Any]] = []
        seen_ids: set[str] = set()
        used_chars = 0
        for item in seed_results:
            item_id = item["id"]
            item_chars = len(item.get("text", ""))
            if item_id in seen_ids or len(expanded) >= max_chunks:
                break
            if used_chars + item_chars > max_chars:
                break
            expanded.append(item)
            seen_ids.add(item_id)
            used_chars += item_chars
        for linked_file in list(self.linked_files(seed_files))[:max_files]:
            for item in self.vault_service.get_approved_chunks(linked_file, filters):
                if item["id"] in seen_ids:
                    continue
                if len(expanded) >= max_chunks or used_chars + len(item["text"]) > max_chars:
                    return expanded
                item["score"] = 0.0
                item["metadata"] = {**item["metadata"], "retrieval_stage": "graph_expansion"}
                expanded.append(item)
                seen_ids.add(item["id"])
                used_chars += len(item["text"])
        return expanded

    def _load_edges(self) -> list[dict[str, str]]:
        if not self.graph_path.exists():
            return []
        try:
            data = json.loads(self.graph_path.read_text(encoding="utf-8"))
            return data if isinstance(data, list) else []
        except (OSError, json.JSONDecodeError):
            return []

    @staticmethod
    def _normalize(name: str) -> str:
        return Path(name).stem.casefold()
