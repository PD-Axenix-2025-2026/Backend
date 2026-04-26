import httpx
from app.clients.base import BaseHttpClientFactory
from pydantic import BaseModel


class RzdConfig(BaseModel):
    """Конфигурация для API РЖД"""

    language: str = "ru"
    timeout: float = 30.0
    debug: bool = False
    user_agent: str | None = None
    referer: str | None = None
    proxy: str | None = None


class RzdHttpClientFactory(BaseHttpClientFactory):
    def __init__(self, config: RzdConfig) -> None:
        super().__init__()
        self._config = config

    def _build_client(self) -> httpx.AsyncClient:
        headers = {
            "Accept": "application/json",
        }
        if self._config.user_agent:
            headers["User-Agent"] = self._config.user_agent
        if self._config.referer:
            headers["Referer"] = self._config.referer

        client = httpx.AsyncClient(
            timeout=self._config.timeout,
            headers=headers,
            follow_redirects=True,
            verify=False,
            proxy=self._config.proxy,
        )

        return client
