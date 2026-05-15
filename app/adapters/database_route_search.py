import logging
from collections.abc import Sequence
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.models.enums import LocationType
from app.models.location import Location
from app.repositories.location_repository import LocationRepository
from app.repositories.route_segment_repository import RouteSegmentRepository
from app.services.search.contracts import RouteCandidate, RouteSearchCriteria
from app.services.search.planner import (
    EndpointScope,
    build_database_route_candidates,
    build_planner_constraints,
    resolve_transfer_cap,
)

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
            location_repository = LocationRepository(session)
            repository = RouteSegmentRepository(session)
            transfer_cap = resolve_transfer_cap(criteria)
            endpoint_scope = await _load_endpoint_scope(
                repository=location_repository,
                criteria=criteria,
            )
            if (
                not endpoint_scope.origin_endpoint_ids
                or not endpoint_scope.destination_endpoint_ids
            ):
                logger.debug(
                    "Database route planning skipped because endpoint scope is empty"
                )
                return []

            planning_window_days = 1 if transfer_cap == 0 else transfer_cap + 2
            segments = await repository.list_active_for_planning(
                criteria,
                planning_window_days=planning_window_days,
            )
            routes = build_database_route_candidates(
                criteria=criteria,
                segments=segments,
                endpoint_scope=endpoint_scope,
                constraints=build_planner_constraints(criteria),
            )
        logger.debug(
            "Database route search completed candidate_count=%s",
            len(routes),
        )
        return routes


async def _load_endpoint_scope(
    *,
    repository: LocationRepository,
    criteria: RouteSearchCriteria,
) -> EndpointScope:
    origin = await repository.get_by_id(criteria.origin_id)
    destination = await repository.get_by_id(criteria.destination_id)
    if origin is None or destination is None:
        return EndpointScope(
            origin_endpoint_ids=frozenset(),
            destination_endpoint_ids=frozenset(),
        )

    child_locations = await _load_child_locations(
        repository=repository,
        locations=(origin, destination),
    )
    child_locations_by_parent: dict[UUID | None, list[Location]] = {}
    for child in child_locations:
        child_locations_by_parent.setdefault(
            child.parent_location_id,
            [],
        ).append(child)

    return EndpointScope(
        origin_endpoint_ids=_expand_endpoint_ids(origin, child_locations_by_parent),
        destination_endpoint_ids=_expand_endpoint_ids(
            destination,
            child_locations_by_parent,
        ),
    )


async def _load_child_locations(
    *,
    repository: LocationRepository,
    locations: Sequence[Location],
) -> list[Location]:
    city_ids = [
        location.id
        for location in locations
        if location.location_type == LocationType.city
    ]
    if not city_ids:
        return []

    statement = select(Location).where(Location.parent_location_id.in_(city_ids))
    result = await repository.session.execute(statement)
    return list(result.scalars().all())


def _expand_endpoint_ids(
    location: Location,
    child_locations_by_parent: dict[UUID | None, list[Location]],
) -> frozenset[UUID]:
    endpoint_ids = {location.id}
    if location.location_type == LocationType.city:
        endpoint_ids.update(
            child.id for child in child_locations_by_parent.get(location.id, [])
        )
    return frozenset(endpoint_ids)


__all__ = [
    "DatabaseRouteSearchAdapter",
    "EndpointScope",
    "RouteSegmentRepository",
]
