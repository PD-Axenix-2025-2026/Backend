import pytest

from app.core.config import get_settings
import app.main as app_main


class _FakeScalarResult:
    def all(self) -> list[object]:
        return []


class _FakeExecuteResult:
    def scalars(self) -> _FakeScalarResult:
        return _FakeScalarResult()

    def scalar(self) -> int:
        return 1


class _FakeSession:
    async def execute(self, *_args, **_kwargs) -> _FakeExecuteResult:
        return _FakeExecuteResult()


class _FakeSessionContext:
    async def __aenter__(self) -> _FakeSession:
        return _FakeSession()

    async def __aexit__(self, exc_type, exc, tb) -> None:
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


@pytest.fixture
def app(tmp_path, monkeypatch):
    monkeypatch.setenv(
        "PDAXENIX_DATABASE_URL",
        "postgresql+asyncpg://postgres:postgres@localhost:5432/pdaxenix_test",
    )
    monkeypatch.setenv("PDAXENIX_APP_ENV", "test")
    monkeypatch.setenv("PDAXENIX_REDIS_URL", "redis://redis:6379/0")
    fake_redis_client = _FakeRedisClient()
    monkeypatch.setattr(app_main, "build_engine", lambda _settings: object())
    monkeypatch.setattr(app_main, "build_session_factory", lambda _engine: _FakeSessionFactory())
    monkeypatch.setattr(app_main, "build_redis_client", lambda _settings: fake_redis_client)
    monkeypatch.setattr(app_main, "init_models", lambda _engine: _async_noop())
    monkeypatch.setattr(app_main, "dispose_engine", lambda _engine: _async_noop())
    monkeypatch.setattr(app_main, "dispose_redis_client", lambda _redis_client: _async_noop())
    get_settings.cache_clear()

    application = app_main.create_app()
    yield application

    get_settings.cache_clear()


async def _async_noop() -> None:
    return None
