from __future__ import annotations

import uuid
from collections.abc import Mapping, Sequence
from datetime import datetime
from decimal import Decimal

from app.models.location import Location
from app.services.search.contracts import ProviderRouteSegment


def build_provider_segment_id(
    *,
    source: str,
    origin_code: str | None,
    destination_code: str | None,
    departure_at: datetime,
    arrival_at: datetime,
    segment_code: str | None,
) -> uuid.UUID:
    payload = "|".join(
        (
            source,
            origin_code or "",
            destination_code or "",
            departure_at.isoformat(),
            arrival_at.isoformat(),
            segment_code or "",
        )
    )
    return uuid.uuid5(uuid.NAMESPACE_URL, payload)


def resolve_provider_location(
    *,
    code: str | None,
    requested_code: str | None,
    requested_location: Location | None,
    locations_by_code: Mapping[str, Location],
) -> Location | None:
    if code and code in locations_by_code:
        return locations_by_code[code]
    if code and requested_code and code == requested_code:
        return requested_location
    return requested_location if code is None else None


def resolve_provider_route_total_price(
    *,
    route_total_price: Decimal | None,
    provider_segments: Sequence[ProviderRouteSegment],
) -> Decimal | None:
    if route_total_price is not None:
        return route_total_price
    if any(segment.price_amount is None for segment in provider_segments):
        return None

    prices = [
        segment.price_amount
        for segment in provider_segments
        if segment.price_amount is not None
    ]

    if len(prices) != len(provider_segments):
        return None

    return sum(prices, start=Decimal("0"))


def resolve_provider_route_duration_minutes(
    *,
    explicit_duration_minutes: int | None,
    provider_segments: Sequence[ProviderRouteSegment],
) -> int:
    if explicit_duration_minutes is not None and explicit_duration_minutes > 0:
        return explicit_duration_minutes
    first_segment = provider_segments[0]
    last_segment = provider_segments[-1]
    return int(
        (last_segment.arrival_at - first_segment.departure_at).total_seconds() // 60
    )


def has_transfer_marker(
    route_data: Mapping[str, object],
    marker_keys: Sequence[str],
) -> bool:
    for key in marker_keys:
        value = route_data.get(key)
        if isinstance(value, bool):
            return value
        if isinstance(value, int):
            return value > 0
        if isinstance(value, str):
            normalized = value.strip().lower()
            if normalized in {"true", "1", "yes"}:
                return True
            if normalized in {"false", "0", "no"}:
                return False
    return False
