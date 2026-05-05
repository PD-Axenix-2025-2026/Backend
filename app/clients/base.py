import asyncio
from abc import ABC, abstractmethod

import httpx


class BaseHttpClientFactory(ABC):
    """Базовый класс для фабрик HTTP клиентов (создаются лениво)"""

    def __init__(self) -> None:
        self._client: httpx.AsyncClient | None = None
        self._lock = asyncio.Lock()

    @abstractmethod
    def _build_client(self) -> httpx.AsyncClient:
        """Создание HTTP клиента"""
        pass

    async def get(self) -> httpx.AsyncClient:
        """Получение клиента (ленивая инициализация)"""
        if self._client and not self._client.is_closed:
            return self._client

        async with self._lock:
            if self._client is None or self._client.is_closed:
                self._client = self._build_client()
            return self._client

    async def aclose(self) -> None:
        """Закрытие клиента"""
        if self._client and not self._client.is_closed:
            await self._client.aclose()
            self._client = None
