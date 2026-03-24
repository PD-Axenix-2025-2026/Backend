from collections.abc import Callable, Generator

import app.main as app_main
import pytest
from _pytest.monkeypatch import MonkeyPatch
from app.core.config import get_settings
from fastapi import FastAPI

TEST_DATABASE_URL = (
    "postgresql+asyncpg://postgres:postgres@localhost:5432/pdaxenix_test"
)
TEST_REDIS_URL = "redis://redis:6379/0"


class _FakeScalarResult:
    def all(self) -> list[object]:
        return []


class _FakeExecuteResult:
    def scalars(self) -> _FakeScalarResult:
        return _FakeScalarResult()

    def scalar(self) -> int:
        return 1


class _FakeSession:
    async def execute(self, *_args: object, **_kwargs: object) -> _FakeExecuteResult:
        return _FakeExecuteResult()


class _FakeSessionContext:
    async def __aenter__(self) -> _FakeSession:
        return _FakeSession()

    async def __aexit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        tb: object,
    ) -> None:
        return None


class _FakeSessionFactory:
    def __call__(self) -> _FakeSessionContext:
        return _FakeSessionContext()


class _FakeRedisClient:
    def __init__(self) -> None:
        self.ping_calls = 0
        self.closed = False

    async def ping(self) -> bool:
        self.ping_calls += 1
        return True

    async def aclose(self) -> None:
        self.closed = True


def _configure_test_environment(monkeypatch: MonkeyPatch) -> None:
    monkeypatch.setenv("PDAXENIX_DATABASE_URL", TEST_DATABASE_URL)
    monkeypatch.setenv("PDAXENIX_APP_ENV", "test")
    monkeypatch.setenv("PDAXENIX_REDIS_URL", TEST_REDIS_URL)


def _patch_app_dependencies(
    monkeypatch: MonkeyPatch,
    fake_redis_client: _FakeRedisClient,
) -> None:
    monkeypatch.setattr(app_main, "build_engine", _build_fake_engine)
    monkeypatch.setattr(app_main, "build_session_factory", _build_fake_session_factory)
    monkeypatch.setattr(
        app_main,
        "build_redis_client",
        _build_fake_redis_client(fake_redis_client),
    )
    monkeypatch.setattr(app_main, "init_models", _async_noop)
    monkeypatch.setattr(app_main, "dispose_engine", _async_noop)
    monkeypatch.setattr(app_main, "dispose_redis_client", _async_noop)


@pytest.fixture
def app(monkeypatch: MonkeyPatch) -> Generator[FastAPI, None, None]:
    fake_redis_client = _FakeRedisClient()

    _configure_test_environment(monkeypatch)
    _patch_app_dependencies(monkeypatch, fake_redis_client)

    get_settings.cache_clear()
    application = app_main.create_app()

    try:
        yield application
    finally:
        get_settings.cache_clear()


def _build_fake_engine(_settings: object) -> object:
    return object()


def _build_fake_session_factory(_engine: object) -> _FakeSessionFactory:
    return _FakeSessionFactory()


def _build_fake_redis_client(
    fake_redis_client: _FakeRedisClient,
) -> Callable[[object], _FakeRedisClient]:
    def factory(_settings: object) -> _FakeRedisClient:
        return fake_redis_client

    return factory


async def _async_noop(*_args: object) -> None:
    return None
