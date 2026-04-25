from __future__ import annotations

from collections.abc import Sequence
from decimal import Decimal
from typing import cast
from uuid import UUID, uuid4

from app.models.enums import TransportType
from app.models.route_segment import RouteSegment
from app.services.contracts import RouteCandidate
from app.services.search_store_models import (
    MoneySnapshot,
    RouteSegmentSnapshot,
    RouteSnapshot,
)


def resolve_candidate_segments(
    candidate: RouteCandidate,
    *,
    segments_by_id: dict[UUID, RouteSegment],
) -> list[RouteSegment] | None:
    if candidate.resolved_segments:
        return list(candidate.resolved_segments)
    resolved_segments = [
        segments_by_id.get(segment_id) for segment_id in candidate.segment_ids
    ]
    if any(segment is None for segment in resolved_segments):
        return None
    return cast(list[RouteSegment], resolved_segments)


def build_route_snapshot(
    *,
    search_id: UUID,
    candidate: RouteCandidate,
    segments: Sequence[RouteSegment],
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
        total_price=MoneySnapshot(
            amount=resolve_total_price(candidate, segments),
            currency=first_segment.currency_code,
        ),
        transport_types=collect_transport_types(segments),
        segments=tuple(build_segment_snapshot(segment) for segment in segments),
    )


def build_segment_snapshot(segment: RouteSegment) -> RouteSegmentSnapshot:
    return RouteSegmentSnapshot(
        segment_id=segment.id,
        transport_type=segment.transport_type,
        carrier=segment.carrier.name,
        carrier_code=segment.carrier.code,
        segment_code=segment.segment_code,
        origin_id=segment.origin_location.id,
        origin_code=segment.origin_location.code,
        origin_label=segment.origin_location.name,
        destination_id=segment.destination_location.id,
        destination_code=segment.destination_location.code,
        destination_label=segment.destination_location.name,
        departure_at=segment.departure_at,
        arrival_at=segment.arrival_at,
        duration_minutes=segment.duration_minutes,
        price=MoneySnapshot(
            amount=segment.price_amount,
            currency=segment.currency_code,
        ),
        available_seats=segment.available_seats,
        source_system=segment.source_system,
        source_record_id=segment.source_record_id,
        valid_from=segment.valid_from,
        valid_to=segment.valid_to,
    )


def resolve_total_price(
    candidate: RouteCandidate,
    segments: Sequence[RouteSegment],
) -> Decimal:
    if candidate.total_price is not None:
        return candidate.total_price
    return sum((segment.price_amount for segment in segments), start=Decimal("0"))


def resolve_total_duration_minutes(
    candidate: RouteCandidate,
    segments: Sequence[RouteSegment],
) -> int:
    if candidate.total_duration_minutes is not None:
        return candidate.total_duration_minutes

    first_segment = segments[0]
    last_segment = segments[-1]
    return int(
        (last_segment.arrival_at - first_segment.departure_at).total_seconds() // 60
    )


def collect_transport_types(
    segments: Sequence[RouteSegment],
) -> tuple[TransportType, ...]:
    return tuple(dict.fromkeys(segment.transport_type for segment in segments))


__all__ = [
    "build_segment_snapshot",
    "build_route_snapshot",
    "collect_transport_types",
    "resolve_candidate_segments",
    "resolve_total_duration_minutes",
    "resolve_total_price",
]
