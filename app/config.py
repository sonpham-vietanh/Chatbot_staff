from functools import lru_cache
from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    app_name: str = "Viet Anh Staff Assistant"
    environment: str = "local"
    obsidian_vault_path: Path = Path("Obsidian_Vault")
    draft_review_path: Path = Path("Draft_Review")
    vector_db_path: Path = Path("data/chroma")
    graph_path: Path = Path("data/graph_edges.json")
    vector_collection: str = "staff_knowledge"
    top_k: int = 5
    min_relevance_score: float = 0.22
    graph_max_files: int = 4
    graph_max_chunks: int = 8
    context_max_chars: int = 12000
    embedding_provider: str = "mock"
    llm_provider: str = "mock"
    gemini_api_key: str | None = None
    gemini_model: str = "gemini-2.5-flash"
    gemini_embedding_model: str = "gemini-embedding-001"
    openrouter_api_key: str | None = None
    openrouter_model: str = "google/gemini-2.5-flash"
    openrouter_embedding_model: str = "openai/text-embedding-3-small"
    openrouter_base_url: str = "https://openrouter.ai/api/v1"
    vault_watcher_enabled: bool = True
    vault_watcher_debounce_seconds: float = 2.0
    admin_token: str | None = None

    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    def validate_vault_path(self) -> None:
        path = self.obsidian_vault_path.expanduser()
        if path.suffix.casefold() == ".exe":
            raise ValueError(
                "OBSIDIAN_VAULT_PATH phải là thư mục Vault chứa file .md, không phải Obsidian.exe"
            )
        if not path.exists() or not path.is_dir():
            raise ValueError(f"Không tìm thấy thư mục Obsidian Vault: {path}")


@lru_cache
def get_settings() -> Settings:
    return Settings()
