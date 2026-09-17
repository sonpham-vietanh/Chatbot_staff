from pathlib import Path

from app.rag.embeddings import MockEmbeddingProvider
from app.services.obsidian import ObsidianLoader


def test_mock_embedding_is_deterministic():
    provider = MockEmbeddingProvider(dimensions=16)
    assert provider.embed("nghỉ phép") == provider.embed("nghỉ phép")


def test_extract_wikilinks(tmp_path: Path):
    loader = ObsidianLoader(tmp_path / "vault", tmp_path / "graph.json")
    assert loader.extract_wikilinks("Xem [[02_Bao_hiem_Phep]] và [[HR#Owner|HR]]") == [
        "02_Bao_hiem_Phep",
        "HR",
    ]
