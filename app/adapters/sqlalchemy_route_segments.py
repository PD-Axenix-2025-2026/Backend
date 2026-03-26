import logging
from collections.abc import Sequence
from uuid import UUID

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.models.route_segment import RouteSegment
from app.repositories.route_segment_repository import RouteSegmentRepository

logger = logging.getLogger(__name__)


class SqlAlchemyRouteSegmentReadAdapter:
    def __init__(
        self,
        session_factory: async_sessionmaker[AsyncSession],
    ) -> None:
        self._session_factory = session_factory

    async def list_by_ids(self, segment_ids: Sequence[UUID]) -> list[RouteSegment]:
        logger.debug(
            "Loading route segments by ids through adapter count=%s",
            len(segment_ids),
        )
        async with self._session_factory() as session:
            repository = RouteSegmentRepository(session)
            return await repository.list_by_ids(segment_ids)
