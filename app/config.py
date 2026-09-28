from functools import lru_cache

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    app_name: str = "Viet Anh Staff Assistant"
    environment: str = "local"
    supabase_url: str | None = None
    supabase_service_key: str | None = None
    top_k: int = 20
    min_relevance_score: float = 0.22
    embedding_provider: str = "mock"
    llm_provider: str = "mock"
    gemini_api_key: str | None = None
    gemini_model: str = "gemini-2.5-flash"
    gemini_embedding_model: str = "gemini-embedding-001"
    openrouter_api_key: str | None = None
    openrouter_model: str = "google/gemini-2.5-flash"
    openrouter_embedding_model: str = "openai/text-embedding-3-small"
    openrouter_base_url: str = "https://openrouter.ai/api/v1"
    admin_token: str | None = None
    anthropic_api_key: str | None = None
    vault_path: str | None = None
    claude_cli_path: str | None = None
    """Duong dan toi binary claude native - can thiet tren Windows (npm cli la .CMD,
    bi Claude Agent SDK tu choi chay vi ly do bao mat). Tren Linux server, de trong
    de SDK tu tim trong PATH (cai qua script cai dat chinh thuc se tu them vao PATH)."""
    allowed_email_domains: str | None = None
    """Danh sách domain email công ty được phép đăng ký, cách nhau bởi dấu phẩy
    (vd 'truongvietanh.com,mamnon-vietanh.com'). Để trống = không giới hạn."""

    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    def validate_supabase(self) -> None:
        if not self.supabase_url or not self.supabase_service_key:
            raise ValueError("SUPABASE_URL và SUPABASE_SERVICE_KEY phải được cấu hình trong .env")


@lru_cache
def get_settings() -> Settings:
    return Settings()
