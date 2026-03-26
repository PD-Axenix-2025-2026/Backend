from __future__ import annotations

from collections.abc import Sequence
from decimal import Decimal

from app.api.query_parsers import parse_csv_enum_values
from app.models.enums import TransportType
from app.schemas.searches import SearchCreateRequest
from app.services.models import (
    PassengerCounts,
    RouteSearchCriteria,
    RouteSearchPreferences,
    SearchResultsQuery,
    SearchSortOption,
)


def build_search_criteria(payload: SearchCreateRequest) -> RouteSearchCriteria:
    return RouteSearchCriteria(
        origin_id=payload.origin.id,
        origin_type=payload.origin.type,
        destination_id=payload.destination.id,
        destination_type=payload.destination.type,
        travel_date=payload.date,
        passengers=PassengerCounts(
            adults=payload.passengers.adults,
            children=payload.passengers.children,
            infants=payload.passengers.infants,
        ),
        transport_types=tuple(payload.transport_types),
        preferences=RouteSearchPreferences(
            sort=payload.preferences.sort,
            max_transfers=payload.preferences.max_transfers,
            max_price=payload.preferences.max_price,
            max_duration_minutes=payload.preferences.max_duration_minutes,
        ),
    )


def build_results_query(
    *,
    last_update: int,
    sort: SearchSortOption | None,
    max_price: Decimal | None,
    max_transfers: int | None,
    max_duration_minutes: int | None,
    transport_types: str | None,
    limit: int,
    offset: int,
) -> SearchResultsQuery:
    return SearchResultsQuery(
        last_update=last_update,
        sort=sort,
        max_price=max_price,
        max_transfers=max_transfers,
        max_duration_minutes=max_duration_minutes,
        transport_types=parse_csv_enum_values(
            transport_types,
            enum_type=TransportType,
            parameter_name="transport_types",
        ),
        limit=limit,
        offset=offset,
    )


def build_create_search_log_fields(
    criteria: RouteSearchCriteria,
) -> dict[str, object]:
    return {
        "origin_id": str(criteria.origin_id),
        "destination_id": str(criteria.destination_id),
        "travel_date": criteria.travel_date.isoformat(),
        "passengers_total": criteria.passengers.total,
        "transport_types": _serialize_transport_types(criteria.transport_types),
    }


def build_results_request_log_fields(
    *,
    last_update: int,
    sort: SearchSortOption | None,
    max_price: Decimal | None,
    max_transfers: int | None,
    max_duration_minutes: int | None,
    transport_types: str | None,
    limit: int,
    offset: int,
) -> dict[str, object]:
    return {
        "last_update": last_update,
        "sort": "-" if sort is None else sort.value,
        "max_price": max_price,
        "max_transfers": max_transfers,
        "max_duration_minutes": max_duration_minutes,
        "transport_types": transport_types or "-",
        "limit": limit,
        "offset": offset,
    }


def _serialize_transport_types(
    transport_types: Sequence[TransportType],
) -> list[str]:
    values = [transport_type.value for transport_type in transport_types]
    return values or ["all"]


__all__ = [
    "build_create_search_log_fields",
    "build_results_query",
    "build_results_request_log_fields",
    "build_search_criteria",
]
