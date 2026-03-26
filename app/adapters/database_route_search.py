import logging

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.repositories.route_segment_repository import RouteSegmentRepository
from app.services.models import RouteCandidate, RouteSearchCriteria

logger = logging.getLogger(__name__)


class DatabaseRouteSearchAdapter:
    def __init__(
        self,
        session_factory: async_sessionmaker[AsyncSession],
    ) -> None:
        self._session_factory = session_factory

    async def search(self, criteria: RouteSearchCriteria) -> list[RouteCandidate]:
        logger.debug(
            (
                "Database route search started "
                "origin_id=%s destination_id=%s travel_date=%s"
            ),
            criteria.origin_id,
            criteria.destination_id,
            criteria.travel_date,
        )
        async with self._session_factory() as session:
            repository = RouteSegmentRepository(session)
            routes = await repository.find_direct_candidates(criteria)
        logger.debug(
            "Database route search completed candidate_count=%s",
            len(routes),
        )
        return routes
