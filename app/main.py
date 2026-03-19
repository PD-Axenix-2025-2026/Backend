from contextlib import asynccontextmanager

from fastapi import FastAPI

from app.api.router import api_router
from app.core.config import get_settings
from app.core.container import AppContainer
from app.core.database import build_engine, build_session_factory, dispose_engine, init_models
from app.core.logging import configure_logging
from app.core.redis import build_redis_client, dispose_redis_client


@asynccontextmanager
async def lifespan(app: FastAPI):
    settings = get_settings()
    configure_logging(settings)

    engine = build_engine(settings)
    session_factory = build_session_factory(engine)
    redis_client = build_redis_client(settings)
    container = AppContainer(
        settings=settings,
        engine=engine,
        session_factory=session_factory,
        redis_client=redis_client,
    )

    app.state.container = container
    await init_models(engine)

    try:
        yield
    finally:
        await dispose_redis_client(redis_client)
        await dispose_engine(engine)


def create_app() -> FastAPI:
    settings = get_settings()
    app = FastAPI(
        title=settings.app_name,
        debug=settings.debug,
        lifespan=lifespan,
    )
    app.include_router(api_router, prefix=settings.api_prefix)
    return app


app = create_app()
