from fastapi import APIRouter, Request
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
    async with container.session_factory() as session:
        await session.execute(text("SELECT 1"))
    return HealthResponse(status="ok")
