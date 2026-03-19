import pytest
from httpx import ASGITransport, AsyncClient


@pytest.mark.asyncio
async def test_healthcheck_returns_ok(app) -> None:
    async with app.router.lifespan_context(app):
        async with AsyncClient(
            transport=ASGITransport(app=app),
            base_url="http://testserver",
        ) as client:
            response = await client.get("/api/health")

    assert response.status_code == 200
    assert response.json() == {"status": "ok"}


@pytest.mark.asyncio
async def test_readiness_returns_ok(app) -> None:
    async with app.router.lifespan_context(app):
        container = app.state.container

        async with AsyncClient(
            transport=ASGITransport(app=app),
            base_url="http://testserver",
        ) as client:
            response = await client.get("/api/ready")

    assert response.status_code == 200
    assert response.json() == {"status": "ok"}
    assert container.redis_client.ping_calls == 1
