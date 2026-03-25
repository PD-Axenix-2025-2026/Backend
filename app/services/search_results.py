from __future__ import annotations

from collections import Counter
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal
from uuid import UUID

from app.models.enums import TransportType
from app.services.contracts import (
    RouteSearchCriteria,
    SearchResultsQuery,
    SearchSortOption,
    SearchStatus,
)
from app.services.search_store_models import RouteSnapshot, SearchRecord, utc_now


@dataclass(slots=True, frozen=True)
class SearchHandle:
    search_id: UUID
    status: SearchStatus
    results_url: str
    poll_after_ms: int
    expires_at: datetime


@dataclass(slots=True, frozen=True)
class TransportTypeFacet:
    value: TransportType
    count: int


@dataclass(slots=True, frozen=True)
class TransferFacet:
    value: int
    count: int


@dataclass(slots=True, frozen=True)
class DecimalRange:
    min: Decimal | None
    max: Decimal | None


@dataclass(slots=True, frozen=True)
class IntegerRange:
    min: int | None
    max: int | None


@dataclass(slots=True, frozen=True)
class RouteListView:
    route: RouteSnapshot
    labels: tuple[str, ...]


@dataclass(slots=True, frozen=True)
class SearchResultsPage:
    search_id: UUID
    status: SearchStatus
    is_complete: bool
    last_update: int
    total_found: int
    currency: str
    stale_after_sec: int
    transport_type_facets: tuple[TransportTypeFacet, ...]
    transfer_facets: tuple[TransferFacet, ...]
    price_range: DecimalRange
    duration_range: IntegerRange
    items: tuple[RouteListView, ...]
    error_message: str | None = None


@dataclass(slots=True, frozen=True)
class CheckoutLinkInfo:
    method: str
    url: str
    expires_at: datetime
    params: dict[str, str] | None = None


@dataclass(slots=True, frozen=True)
class EffectiveResultsQuery:
    sort: SearchSortOption
    transport_types: tuple[TransportType, ...]
    max_price: Decimal | None
    max_transfers: int | None
    max_duration_minutes: int | None


def build_effective_results_query(
    criteria: RouteSearchCriteria,
    query: SearchResultsQuery,
) -> EffectiveResultsQuery:
    return EffectiveResultsQuery(
        sort=query.sort or criteria.preferences.sort,
        transport_types=query.transport_types or criteria.transport_types,
        max_price=(
            query.max_price
            if query.max_price is not None
            else criteria.preferences.max_price
        ),
        max_transfers=(
            query.max_transfers
            if query.max_transfers is not None
            else criteria.preferences.max_transfers
        ),
        max_duration_minutes=(
            query.max_duration_minutes
            if query.max_duration_minutes is not None
            else criteria.preferences.max_duration_minutes
        ),
    )


def collect_visible_routes(
    routes: Sequence[RouteSnapshot],
    effective_query: EffectiveResultsQuery,
) -> list[RouteSnapshot]:
    return sort_routes(
        filter_routes(routes=routes, effective_query=effective_query),
        sort=effective_query.sort,
    )


def filter_routes(
    *,
    routes: Sequence[RouteSnapshot],
    effective_query: EffectiveResultsQuery,
) -> list[RouteSnapshot]:
    filtered_routes = list(routes)
    if effective_query.transport_types:
        allowed_types = set(effective_query.transport_types)
        filtered_routes = [
            route
            for route in filtered_routes
            if set(route.transport_types).issubset(allowed_types)
        ]
    if effective_query.max_price is not None:
        filtered_routes = [
            route
            for route in filtered_routes
            if route.total_price.amount <= effective_query.max_price
        ]
    if effective_query.max_transfers is not None:
        filtered_routes = [
            route
            for route in filtered_routes
            if route.transfers <= effective_query.max_transfers
        ]
    if effective_query.max_duration_minutes is not None:
        filtered_routes = [
            route
            for route in filtered_routes
            if route.duration_minutes <= effective_query.max_duration_minutes
        ]
    return filtered_routes


def sort_routes(
    routes: Sequence[RouteSnapshot],
    *,
    sort: SearchSortOption,
) -> list[RouteSnapshot]:
    if sort == SearchSortOption.duration:
        return sorted(routes, key=_duration_sort_key)
    return sorted(routes, key=_price_sort_key)


def build_route_list_views(
    routes: Sequence[RouteSnapshot],
    *,
    sort: SearchSortOption,
) -> tuple[RouteListView, ...]:
    return tuple(
        RouteListView(
            route=route,
            labels=build_labels(route=route, sort=sort, index=index),
        )
        for index, route in enumerate(routes)
    )


def build_results_page(
    *,
    record: SearchRecord,
    routes: Sequence[RouteSnapshot],
    route_views: tuple[RouteListView, ...],
    query: SearchResultsQuery,
) -> SearchResultsPage:
    paginated_items = route_views[query.offset : query.offset + query.limit]

    return SearchResultsPage(
        search_id=record.search_id,
        status=record.status,
        is_complete=record.status in {SearchStatus.complete, SearchStatus.failed},
        last_update=record.last_update,
        total_found=len(routes),
        currency=_resolve_currency(routes),
        stale_after_sec=max(0, int((record.expires_at - utc_now()).total_seconds())),
        transport_type_facets=build_transport_type_facets(routes),
        transfer_facets=build_transfer_facets(routes),
        price_range=build_price_range(routes),
        duration_range=build_duration_range(routes),
        items=paginated_items,
        error_message=record.error_message,
    )


def build_labels(
    *,
    route: RouteSnapshot,
    sort: SearchSortOption,
    index: int,
) -> tuple[str, ...]:
    labels = list(route.base_labels)
    if sort == SearchSortOption.best and index == 0:
        labels.insert(0, "best")
    return tuple(labels)


def build_transport_type_facets(
    routes: Sequence[RouteSnapshot],
) -> tuple[TransportTypeFacet, ...]:
    counter = Counter(
        transport_type
        for route in routes
        for transport_type in dict.fromkeys(route.transport_types)
    )
    return tuple(
        TransportTypeFacet(value=transport_type, count=count)
        for transport_type, count in sorted(counter.items(), key=lambda item: item[0])
    )


def build_transfer_facets(
    routes: Sequence[RouteSnapshot],
) -> tuple[TransferFacet, ...]:
    counter = Counter(route.transfers for route in routes)
    return tuple(
        TransferFacet(value=transfers, count=count)
        for transfers, count in sorted(counter.items())
    )


def build_price_range(routes: Sequence[RouteSnapshot]) -> DecimalRange:
    prices = [route.total_price.amount for route in routes]
    if not prices:
        return DecimalRange(min=None, max=None)
    return DecimalRange(min=min(prices), max=max(prices))


def build_duration_range(routes: Sequence[RouteSnapshot]) -> IntegerRange:
    durations = [route.duration_minutes for route in routes]
    if not durations:
        return IntegerRange(min=None, max=None)
    return IntegerRange(min=min(durations), max=max(durations))


def _price_sort_key(route: RouteSnapshot) -> tuple[Decimal, int, int, datetime]:
    return (
        route.total_price.amount,
        route.duration_minutes,
        route.transfers,
        route.departure_at,
    )


def _duration_sort_key(route: RouteSnapshot) -> tuple[int, Decimal, int, datetime]:
    return (
        route.duration_minutes,
        route.total_price.amount,
        route.transfers,
        route.departure_at,
    )


def _resolve_currency(routes: Sequence[RouteSnapshot]) -> str:
    if not routes:
        return "RUB"
    return routes[0].total_price.currency


__all__ = [
    "CheckoutLinkInfo",
    "DecimalRange",
    "EffectiveResultsQuery",
    "IntegerRange",
    "RouteListView",
    "SearchHandle",
    "SearchResultsPage",
    "TransferFacet",
    "TransportTypeFacet",
    "build_effective_results_query",
    "build_results_page",
    "build_route_list_views",
    "collect_visible_routes",
]
