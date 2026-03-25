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


def build_engine(settings: Settings) -> AsyncEngine:
    return create_async_engine(
        settings.database_url,
        echo=settings.sql_echo,
        future=True,
    )


def build_session_factory(engine: AsyncEngine) -> async_sessionmaker[AsyncSession]:
    return async_sessionmaker(
        bind=engine,
        class_=AsyncSession,
        expire_on_commit=False,
        autoflush=False,
    )


async def init_models(engine: AsyncEngine) -> None:
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)


async def dispose_engine(engine: AsyncEngine) -> None:
    await engine.dispose()
