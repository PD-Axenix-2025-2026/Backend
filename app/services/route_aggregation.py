from app.providers.base import RouteProvider
from app.services.contracts import RouteCandidate, RouteSearchCriteria


class RouteAggregationService:
    def __init__(self, providers: list[RouteProvider]) -> None:
        self._providers = providers

    async def search(self, criteria: RouteSearchCriteria) -> list[RouteCandidate]:
        aggregated: list[RouteCandidate] = []
        seen: set[tuple[str, tuple[int, ...]]] = set()

        for provider in self._providers:
            routes = await provider.search_routes(criteria)
            for route in routes:
                route_key = (route.source, route.segment_ids)
                if route_key in seen:
                    continue
                seen.add(route_key)
                aggregated.append(route)

        aggregated.sort(
            key=lambda item: (
                item.total_price is None,
                item.total_price,
                item.total_duration_minutes is None,
                item.total_duration_minutes,
            )
        )
        return aggregated
