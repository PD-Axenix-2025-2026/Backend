from app.providers.base import RouteProvider
from app.repositories.route_segment_repository import RouteSegmentRepository
from app.services.contracts import RouteCandidate, RouteSearchCriteria


class DatabaseRouteProvider(RouteProvider):
    def __init__(self, repository: RouteSegmentRepository) -> None:
        self._repository = repository

    async def search_routes(
        self,
        criteria: RouteSearchCriteria,
    ) -> list[RouteCandidate]:
        return await self._repository.find_direct_candidates(criteria)
