import logging

from app.providers.base import RouteProvider
from app.repositories.route_segment_repository import RouteSegmentRepository
from app.services.contracts import RouteCandidate, RouteSearchCriteria

logger = logging.getLogger(__name__)


class DatabaseRouteProvider(RouteProvider):
    def __init__(self, repository: RouteSegmentRepository) -> None:
        self._repository = repository

    async def search_routes(
        self,
        criteria: RouteSearchCriteria,
    ) -> list[RouteCandidate]:
        logger.debug(
            (
                "Database route provider search started "
                "origin_id=%s destination_id=%s travel_date=%s"
            ),
            criteria.origin_id,
            criteria.destination_id,
            criteria.travel_date,
        )
        routes = await self._repository.find_direct_candidates(criteria)
        logger.debug(
            "Database route provider search completed candidate_count=%s",
            len(routes),
        )
        return routes
