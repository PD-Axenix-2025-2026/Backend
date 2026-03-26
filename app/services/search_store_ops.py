from __future__ import annotations

from collections.abc import Mapping
from datetime import datetime
from uuid import UUID

from app.services.contracts import RouteSearchCriteria, SearchStatus
from app.services.search_store_models import (
    RouteNotFoundError,
    RouteSnapshot,
    SearchNotFoundError,
    SearchRecord,
    utc_now,
)


def create_pending_record(
    *,
    search_id: UUID,
    criteria: RouteSearchCriteria,
    expires_at: datetime,
) -> SearchRecord:
    now = utc_now()
    return SearchRecord(
        search_id=search_id,
        criteria=criteria,
        created_at=now,
        updated_at=now,
        expires_at=expires_at,
        status=SearchStatus.pending,
        last_update=0,
    )


def cleanup_expired_searches(
    searches: dict[UUID, SearchRecord],
    route_index: dict[UUID, UUID],
) -> tuple[UUID, ...]:
    expired_search_ids = tuple(
        search_id
        for search_id, record in searches.items()
        if is_expired(record)
    )
    for search_id in expired_search_ids:
        remove_search(searches, route_index, search_id=search_id)
    return expired_search_ids


def require_active_search(
    searches: dict[UUID, SearchRecord],
    route_index: dict[UUID, UUID],
    *,
    search_id: UUID,
) -> SearchRecord:
    record = searches.get(search_id)
    if record is None:
        raise SearchNotFoundError(search_id)
    if is_expired(record):
        remove_search(searches, route_index, search_id=search_id)
        raise SearchNotFoundError(search_id)
    return record


def require_indexed_search_id(
    route_index: Mapping[UUID, UUID],
    *,
    route_id: UUID,
) -> UUID:
    search_id = route_index.get(route_id)
    if search_id is None:
        raise RouteNotFoundError(route_id)
    return search_id


def require_route(record: SearchRecord, *, route_id: UUID) -> RouteSnapshot:
    for route in record.routes:
        if route.route_id == route_id:
            return route
    raise RouteNotFoundError(route_id)


def index_routes(
    route_index: dict[UUID, UUID],
    *,
    search_id: UUID,
    routes: tuple[RouteSnapshot, ...],
) -> None:
    for route in routes:
        route_index[route.route_id] = search_id


def unindex_routes(
    route_index: dict[UUID, UUID],
    routes: tuple[RouteSnapshot, ...],
) -> None:
    for route in routes:
        route_index.pop(route.route_id, None)


def remove_search(
    searches: dict[UUID, SearchRecord],
    route_index: dict[UUID, UUID],
    *,
    search_id: UUID,
) -> bool:
    record = searches.pop(search_id, None)
    if record is None:
        return False
    unindex_routes(route_index, record.routes)
    return True


def is_expired(record: SearchRecord) -> bool:
    return record.expires_at <= utc_now()


__all__ = [
    "cleanup_expired_searches",
    "create_pending_record",
    "index_routes",
    "is_expired",
    "remove_search",
    "require_active_search",
    "require_indexed_search_id",
    "require_route",
    "unindex_routes",
]
