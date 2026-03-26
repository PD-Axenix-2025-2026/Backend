import logging
from uuid import UUID

from app.providers.base import RouteProvider
from app.services.contracts import RouteCandidate, RouteSearchCriteria

logger = logging.getLogger(__name__)


class RouteAggregationService:
    def __init__(self, providers: list[RouteProvider]) -> None:
        self._providers = providers

    async def search(self, criteria: RouteSearchCriteria) -> list[RouteCandidate]:
        logger.debug(
            (
                "Aggregating routes "
                "provider_count=%s origin_id=%s destination_id=%s travel_date=%s"
            ),
            len(self._providers),
            criteria.origin_id,
            criteria.destination_id,
            criteria.travel_date,
        )
        aggregated: list[RouteCandidate] = []
        seen: set[tuple[str, tuple[UUID, ...]]] = set()

        for provider in self._providers:
            provider_name = type(provider).__name__
            try:
                routes = await provider.search_routes(criteria)
            except Exception:
                logger.exception("Route provider failed provider=%s", provider_name)
                raise
            logger.debug(
                "Route provider returned candidates provider=%s candidate_count=%s",
                provider_name,
                len(routes),
            )
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
        logger.debug("Route aggregation completed route_count=%s", len(aggregated))
        return aggregated
