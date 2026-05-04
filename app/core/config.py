from functools import lru_cache

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict

from app.clients.rzd_client_factory import RzdConfig


class Settings(BaseSettings):
    app_name: str = "Аналитический сервис логистики путешествия"
    app_env: str = "dev"
    debug: bool = False
    log_level: str | None = None
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

    # РЖД API конфигурация
    use_rzd_api: bool = True
    rzd_language: str = "ru"
    rzd_timeout: float = 30.0
    rzd_user_agent: str | None = (
        "Mozilla/5.0 (iPhone; CPU iPhone OS 12_1_2 like Mac OS X)"
    )
    rzd_referer: str | None = "https://ticket.rzd.ru/"
    rzd_proxy: str | None = None

    # Яндекс API конфигурация
    use_yandex_api: bool = True
    yandex_rasp_api_key: str | None = None

    model_config = SettingsConfigDict(
        env_prefix="PDAXENIX_",
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )


@lru_cache
def get_settings() -> Settings:
    return Settings()


def build_rzd_config(settings: Settings) -> RzdConfig:
    return RzdConfig(
        language=settings.rzd_language,
        timeout=settings.rzd_timeout,
        user_agent=settings.rzd_user_agent,
        referer=settings.rzd_referer,
        proxy=settings.rzd_proxy,
    )
