from functools import lru_cache
from uuid import UUID

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
    rzd_language: str = "ru"
    rzd_timeout: float = 30.0
    rzd_user_agent: str | None = (
        "Mozilla/5.0 (iPhone; CPU iPhone OS 12_1_2 like Mac OS X)"
    )
    rzd_referer: str | None = "https://ticket.rzd.ru/"
    rzd_proxy: str | None = None
    rzd_station_mapping: dict[str, str] = Field(
        default_factory=lambda: {
            "fbdba9a4-b76e-50ea-9b62-5f7661766ac2": "2004000",
            "8b3993dd-e50f-58c7-a564-1e1f100c847a": "2000000",
        }
    )

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


def build_rzd_station_mapping(settings: Settings) -> dict[UUID, str]:
    return {UUID(key): value for key, value in settings.rzd_station_mapping.items()}
