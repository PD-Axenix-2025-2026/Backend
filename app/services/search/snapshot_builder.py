from __future__ import annotations

from collections.abc import Sequence
from decimal import Decimal
from typing import cast
from uuid import UUID, uuid4

from app.models.enums import TransportType
from app.models.route_segment import RouteSegment
from app.services.search.contracts import (
    ProviderRouteSegment,
    ResolvedRouteSegment,
    RouteCandidate,
)
from app.services.search.store.models import (
    MoneySnapshot,
    RouteSegmentSnapshot,
    RouteSnapshot,
)


def resolve_candidate_segments(
    candidate: RouteCandidate,
    *,
    segments_by_id: dict[UUID, RouteSegment],
) -> list[ResolvedRouteSegment] | None:
    if candidate.resolved_segments:
        return list(candidate.resolved_segments)
    resolved_segments = [
        segments_by_id.get(segment_id) for segment_id in candidate.segment_ids
    ]
    if any(segment is None for segment in resolved_segments):
        return None
    return cast(list[ResolvedRouteSegment], resolved_segments)


def build_route_snapshot(
    *,
    search_id: UUID,
    candidate: RouteCandidate,
    segments: Sequence[ResolvedRouteSegment],
) -> RouteSnapshot:
    first_segment = segments[0]
    last_segment = segments[-1]

    return RouteSnapshot(
        route_id=uuid4(),
        search_id=search_id,
        source=candidate.source,
        segment_ids=candidate.segment_ids,
        departure_at=first_segment.departure_at,
        arrival_at=last_segment.arrival_at,
        duration_minutes=resolve_total_duration_minutes(candidate, segments),
        transfers=candidate.transfers,
        total_price=build_total_price_snapshot(candidate, segments),
        transport_types=collect_transport_types(segments),
        segments=tuple(build_segment_snapshot(segment) for segment in segments),
    )


def build_segment_snapshot(segment: ResolvedRouteSegment) -> RouteSegmentSnapshot:
    if isinstance(segment, ProviderRouteSegment):
        segment_id = segment.segment_id
        carrier = segment.carrier_name
        carrier_code = segment.carrier_code
        origin_location = segment.origin_location
        destination_location = segment.destination_location
        segment_code = segment.segment_code
        transport_type = segment.transport_type
        departure_at = segment.departure_at
        arrival_at = segment.arrival_at
        duration_minutes = segment.duration_minutes
        price = (
            MoneySnapshot(
                amount=segment.price_amount,
                currency=segment.currency_code,
            )
            if segment.price_amount is not None
            else None
        )
        available_seats = segment.available_seats
        source_system = segment.source_system
        source_record_id = segment.source_record_id
        valid_from = segment.valid_from
        valid_to = segment.valid_to
    else:
        segment_id = segment.id
        carrier = segment.carrier.name
        carrier_code = segment.carrier.code
        origin_location = segment.origin_location
        destination_location = segment.destination_location
        segment_code = segment.segment_code
        transport_type = segment.transport_type
        departure_at = segment.departure_at
        arrival_at = segment.arrival_at
        duration_minutes = segment.duration_minutes
        price = MoneySnapshot(
            amount=segment.price_amount,
            currency=segment.currency_code,
        )
        available_seats = segment.available_seats
        source_system = segment.source_system
        source_record_id = segment.source_record_id
        valid_from = segment.valid_from
        valid_to = segment.valid_to

    return RouteSegmentSnapshot(
        segment_id=segment_id,
        transport_type=transport_type,
        carrier=carrier,
        carrier_code=carrier_code,
        segment_code=segment_code,
        origin_id=origin_location.id,
        origin_code=origin_location.code,
        origin_label=origin_location.name,
        destination_id=destination_location.id,
        destination_code=destination_location.code,
        destination_label=destination_location.name,
        departure_at=departure_at,
        arrival_at=arrival_at,
        duration_minutes=duration_minutes,
        price=price,
        available_seats=available_seats,
        source_system=source_system,
        source_record_id=source_record_id,
        valid_from=valid_from,
        valid_to=valid_to,
    )


def build_total_price_snapshot(
    candidate: RouteCandidate,
    segments: Sequence[ResolvedRouteSegment],
) -> MoneySnapshot | None:
    total_price = resolve_total_price(candidate, segments)
    if total_price is None:
        return None
    return MoneySnapshot(
        amount=total_price,
        currency=segments[0].currency_code,
    )


def resolve_total_price(
    candidate: RouteCandidate,
    segments: Sequence[ResolvedRouteSegment],
) -> Decimal | None:
    if candidate.total_price is not None:
        return candidate.total_price

    total = Decimal("0")
    for segment in segments:
        price_amount = segment.price_amount
        if price_amount is None:
            return None
        total += price_amount

    if not segments:
        return None
    return total


def resolve_total_duration_minutes(
    candidate: RouteCandidate,
    segments: Sequence[ResolvedRouteSegment],
) -> int:
    if candidate.total_duration_minutes is not None:
        return candidate.total_duration_minutes

    first_segment = segments[0]
    last_segment = segments[-1]
    return int(
        (last_segment.arrival_at - first_segment.departure_at).total_seconds() // 60
    )


def collect_transport_types(
    segments: Sequence[ResolvedRouteSegment],
) -> tuple[TransportType, ...]:
    return tuple(dict.fromkeys(segment.transport_type for segment in segments))


__all__ = [
    "build_total_price_snapshot",
    "build_segment_snapshot",
    "build_route_snapshot",
    "collect_transport_types",
    "resolve_candidate_segments",
    "resolve_total_duration_minutes",
    "resolve_total_price",
]
