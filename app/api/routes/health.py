from fastapi import APIRouter, HTTPException, Request
from sqlalchemy import text

from app.schemas.health import HealthResponse

router = APIRouter()


@router.get("/health", response_model=HealthResponse)
async def healthcheck() -> HealthResponse:
    return HealthResponse(status="ok")


@router.get("/ready", response_model=HealthResponse)
async def readiness_check(
    request: Request,
) -> HealthResponse:
    container = request.app.state.container

    try:
        async with container.session_factory() as session:
            await session.execute(text("SELECT 1"))

        if container.redis_client is not None:
            await container.redis_client.ping()
    except Exception as exc:
        raise HTTPException(status_code=503, detail="Infrastructure dependencies unavailable") from exc

    return HealthResponse(status="ok")
