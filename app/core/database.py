import logging
from collections.abc import Sequence

from sqlalchemy import Table
from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)

from app.core.config import Settings
from app.models.base import Base
from app.models.carrier import Carrier
from app.models.location import Location
from app.models.route_segment import RouteSegment

# Ensure SQLAlchemy metadata includes all application models before create_all.
MODEL_REGISTRY = (Carrier, Location, RouteSegment)
logger = logging.getLogger(__name__)


def build_engine(settings: Settings) -> AsyncEngine:
    database_backend = settings.database_url.split(":", maxsplit=1)[0]
    logger.debug(
        "Creating async database engine backend=%s sql_echo=%s",
        database_backend,
        settings.sql_echo,
    )
    return create_async_engine(
        settings.database_url,
        echo=settings.sql_echo,
        future=True,
    )


def build_session_factory(engine: AsyncEngine) -> async_sessionmaker[AsyncSession]:
    logger.debug("Creating async session factory")
    return async_sessionmaker(
        bind=engine,
        class_=AsyncSession,
        expire_on_commit=False,
        autoflush=False,
    )


async def init_models(engine: AsyncEngine) -> None:
    logger.info("Initializing database models model_count=%s", len(MODEL_REGISTRY))
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
    logger.info("Database models initialized")


async def recreate_models(
    engine: AsyncEngine,
    *,
    tables: Sequence[Table] | None = None,
) -> None:
    table_count = len(tables) if tables is not None else len(Base.metadata.tables)
    logger.info("Recreating database models table_count=%s", table_count)
    async with engine.begin() as connection:
        if tables is None:
            await connection.run_sync(Base.metadata.drop_all)
            await connection.run_sync(Base.metadata.create_all)
        else:
            await connection.run_sync(
                lambda sync_connection: Base.metadata.drop_all(
                    sync_connection,
                    tables=tables,
                )
            )
            await connection.run_sync(
                lambda sync_connection: Base.metadata.create_all(
                    sync_connection,
                    tables=tables,
                )
            )
    logger.info("Database models recreated")


async def dispose_engine(engine: AsyncEngine) -> None:
    logger.debug("Disposing async database engine")
    await engine.dispose()
