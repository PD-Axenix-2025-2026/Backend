import logging

import pytest
from app.core.logging import REQUEST_ID_HEADER
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient


@pytest.mark.asyncio
async def test_healthcheck_returns_ok(app: FastAPI) -> None:
    async with AsyncClient(
        transport=ASGITransport(app=app),
        base_url="http://testserver",
    ) as client:
        response = await client.get("/api/health")

    assert response.status_code == 200
    assert response.json() == {"status": "ok"}
    assert response.headers[REQUEST_ID_HEADER]


@pytest.mark.asyncio
async def test_healthcheck_preserves_request_id_header(app: FastAPI) -> None:
    async with AsyncClient(
        transport=ASGITransport(app=app),
        base_url="http://testserver",
    ) as client:
        response = await client.get(
            "/api/health",
            headers={REQUEST_ID_HEADER: "req-test-123"},
        )

    assert response.status_code == 200
    assert response.headers[REQUEST_ID_HEADER] == "req-test-123"


@pytest.mark.asyncio
async def test_healthcheck_writes_access_log(
    app: FastAPI,
    caplog: pytest.LogCaptureFixture,
) -> None:
    caplog.set_level(logging.INFO, logger="app.http")

    async with AsyncClient(
        transport=ASGITransport(app=app),
        base_url="http://testserver",
    ) as client:
        response = await client.get(
            "/api/health",
            headers={REQUEST_ID_HEADER: "req-log-123"},
        )

    access_records = [record for record in caplog.records if record.name == "app.http"]

    assert response.status_code == 200
    assert any(
        "HTTP request completed" in record.getMessage()
        and "status_code=200" in record.getMessage()
        and getattr(record, "request_id", None) == "req-log-123"
        for record in access_records
    )


@pytest.mark.asyncio
async def test_readiness_returns_ok(app: FastAPI) -> None:
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
