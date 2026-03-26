import logging
from uuid import UUID

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.models.enums import LocationType
from app.models.location import Location
from app.repositories.location_repository import LocationRepository

logger = logging.getLogger(__name__)


class SqlAlchemyLocationReadAdapter:
    def __init__(
        self,
        session_factory: async_sessionmaker[AsyncSession],
    ) -> None:
        self._session_factory = session_factory

    async def get_by_id(self, location_id: UUID) -> Location | None:
        logger.debug(
            "Loading location by id through adapter location_id=%s",
            location_id,
        )
        async with self._session_factory() as session:
            repository = LocationRepository(session)
            return await repository.get_by_id(location_id)

    async def list_by_prefix(
        self,
        prefix: str,
        limit: int = 10,
        location_types: tuple[LocationType, ...] = (),
    ) -> list[Location]:
        logger.debug(
            "Loading locations by prefix through adapter prefix=%s limit=%s",
            prefix,
            limit,
        )
        async with self._session_factory() as session:
            repository = LocationRepository(session)
            return await repository.list_by_prefix(
                prefix=prefix,
                limit=limit,
                location_types=location_types,
            )
