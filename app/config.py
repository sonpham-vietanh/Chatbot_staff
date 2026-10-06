from functools import lru_cache

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    app_name: str = "Viet Anh Staff Assistant"
    environment: str = "local"
    supabase_url: str | None = None
    supabase_service_key: str | None = None
    supabase_anon_key: str | None = None
    top_k: int = 20
    public_base_url: str | None = None  # vd https://staffbot.vietanh.org — dùng trong file mô tả API cho Major OS
    min_relevance_score: float = 0.22
    embedding_provider: str = "mock"
    retrieval_mode: str = "vector"
    """vector = tìm theo embeddings (cần mô hình embeddings); lexical = tìm theo từ khoá BM25, KHÔNG cần embeddings
    (kho tri thức nội bộ nhỏ). Xem app/rag/lexical_store.py."""
    embedding_dimensions: int = 1536
    """Số chiều cột vector trong bảng knowledge_chunks; chế độ lexical ghi vector 0 với đúng số chiều này."""
    llm_provider: str = "mock"
    gemini_api_key: str | None = None
    gemini_model: str = "gemini-2.5-flash"
    gemini_embedding_model: str = "gemini-embedding-001"
    openrouter_api_key: str | None = None
    openrouter_model: str = "openai/gpt-6-luna"
    openrouter_embedding_model: str = "openai/text-embedding-3-small"
    openrouter_base_url: str = "https://openrouter.ai/api/v1"
    llm_base_url: str | None = None
    """Endpoint kiểu OpenAI tự dựng (vd. https://xxx/v1) cho LLM_PROVIDER=openai_compatible. KHÔNG công khai địa chỉ này."""
    llm_api_key: str | None = None
    llm_model: str = "cc/claude-sonnet-5-5"
    admin_token: str | None = None
    admin_emails: str | None = None
    """Email (cách nhau bởi dấu phẩy) của admin được sửa mọi note ở trang /quan-ly, kể cả phòng
    "Unassigned". Leader thường được cấp quyền theo phòng trong bảng knowledge_leaders."""
    os_sso_secret: str | None = None
    """Khoá bí mật dùng chung với backend Major OS để ký mã đăng nhập 1 lần (HS256, >= 32 ký tự).
    Để trống = tắt đăng nhập qua Major OS."""
    anthropic_api_key: str | None = None
    vault_path: str | None = None
    vault_ready_marker: str | None = None
    """Ten file o goc vault ma drive-sync tao sau khi keo xong vault lan dau (tren
    server: '.drive-sync-ready'). Dat thi VaultWatcher cho file nay roi moi theo doi;
    de trong khi chay local khong co drive-sync."""
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
