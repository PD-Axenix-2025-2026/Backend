from functools import lru_cache

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    app_name: str = "Аналитический сервис логистики путешествия"
    app_env: str = "dev"
    debug: bool = False
    api_prefix: str = "/api"
    database_url: str = Field(
        default="sqlite+aiosqlite:///./pdaxenix.db",
        description="Async SQLAlchemy connection URL.",
    )
    redis_url: str | None = None
    sql_echo: bool = False
    search_ttl_seconds: int = 300
    search_poll_after_ms: int = 1000
    mock_checkout_base_url: str = "https://example.com/checkout"
    checkout_link_ttl_seconds: int = 180

    model_config = SettingsConfigDict(
        env_prefix="PDAXENIX_",
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )


@lru_cache
def get_settings() -> Settings:
    return Settings()
