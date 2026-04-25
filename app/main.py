import logging
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI

from app.api.middleware import register_request_logging_middleware
from app.api.router import api_router
from app.clients.rzd_client_factory import RzdHttpClientFactory
from app.core.config import build_rzd_config, build_rzd_station_mapping, get_settings
from app.core.container import AppContainer
from app.core.database import (
    build_engine,
    build_session_factory,
    dispose_engine,
    init_models,
)
from app.core.logging import configure_logging
from app.core.redis import build_redis_client, dispose_redis_client

logger = logging.getLogger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    settings = get_settings()
    logger.info(
        "Application startup initiated app_env=%s redis_enabled=%s",
        settings.app_env,
        settings.redis_url is not None,
    )

    engine = build_engine(settings)
    session_factory = build_session_factory(engine)
    redis_client = build_redis_client(settings)

    rzd_config = build_rzd_config(settings)
    station_mapping = build_rzd_station_mapping(settings)

    rzd_http_client_factory = RzdHttpClientFactory(rzd_config)

    container = AppContainer(
        settings=settings,
        engine=engine,
        session_factory=session_factory,
        redis_client=redis_client,
        rzd_http_client_factory=rzd_http_client_factory,
        rzd_config=rzd_config,
        station_code_mapping=station_mapping,
    )

    app.state.container = container
    await init_models(engine)
    logger.info("Application startup completed")

    try:
        yield
    finally:
        logger.info("Application shutdown initiated")
        await container.shutdown()
        await rzd_http_client_factory.aclose()
        await dispose_redis_client(redis_client)
        await dispose_engine(engine)
        logger.info("Application shutdown completed")


def create_app() -> FastAPI:
    settings = get_settings()
    configure_logging(settings)
    app = FastAPI(
        title=settings.app_name,
        debug=settings.debug,
        lifespan=lifespan,
    )
    register_request_logging_middleware(app)
    app.include_router(api_router, prefix=settings.api_prefix)
    return app


app = create_app()
