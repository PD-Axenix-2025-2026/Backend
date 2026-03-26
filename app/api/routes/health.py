import logging

from fastapi import APIRouter, HTTPException, Request
from sqlalchemy import text

from app.schemas.health import HealthResponse

router = APIRouter()
logger = logging.getLogger(__name__)


@router.get("/health", response_model=HealthResponse)
async def healthcheck() -> HealthResponse:
    logger.debug("Healthcheck requested")
    return HealthResponse(status="ok")


@router.get("/ready", response_model=HealthResponse)
async def readiness_check(
    request: Request,
) -> HealthResponse:
    container = request.app.state.container
    logger.debug("Readiness check started")

    try:
        async with container.session_factory() as session:
            await session.execute(text("SELECT 1"))

        if container.redis_client is not None:
            await container.redis_client.ping()
    except Exception as exc:
        logger.warning(
            "Readiness check failed redis_enabled=%s",
            container.redis_client is not None,
            exc_info=True,
        )
        raise HTTPException(
            status_code=503, detail="Infrastructure dependencies unavailable"
        ) from exc

    logger.debug("Readiness check completed successfully")
    return HealthResponse(status="ok")
