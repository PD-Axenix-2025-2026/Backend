from __future__ import annotations

from collections import defaultdict
from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from datetime import date
from decimal import Decimal
from uuid import UUID

from app.models.enums import LocationType
from app.models.location import Location
from app.models.route_segment import RouteSegment
from app.services.search.contracts import RouteCandidate, RouteSearchCriteria

MIN_LAYOVER_MINUTES = 60
MAX_LAYOVER_MINUTES = 720
MAX_TRANSFER_COUNT = 3


@dataclass(slots=True, frozen=True)
class EndpointScope:
    origin_endpoint_ids: frozenset[UUID]
    destination_endpoint_ids: frozenset[UUID]


@dataclass(slots=True, frozen=True)
class PlannerConstraints:
    max_transfers: int
    min_layover_minutes: int = MIN_LAYOVER_MINUTES
    max_layover_minutes: int = MAX_LAYOVER_MINUTES
    max_duration_minutes: int | None = None


@dataclass(slots=True)
class _PlannerIndexes:
    by_origin_location_id: dict[UUID, tuple[RouteSegment, ...]]
    by_origin_city_key: dict[UUID, tuple[RouteSegment, ...]]


def resolve_transfer_cap(criteria: RouteSearchCriteria) -> int:
    raw_value = criteria.preferences.max_transfers
    if raw_value is None:
        return 0
    return min(raw_value, MAX_TRANSFER_COUNT)


def build_planner_constraints(criteria: RouteSearchCriteria) -> PlannerConstraints:
    return PlannerConstraints(
        max_transfers=resolve_transfer_cap(criteria),
        max_duration_minutes=criteria.preferences.max_duration_minutes,
    )


def build_database_route_candidates(
    *,
    criteria: RouteSearchCriteria,
    segments: Sequence[RouteSegment],
    endpoint_scope: EndpointScope,
    constraints: PlannerConstraints,
) -> list[RouteCandidate]:
    if not segments:
        return []

    sorted_segments = tuple(sorted(segments, key=_segment_sort_key))
    indexes = _build_indexes(sorted_segments)
    candidate_keys: set[tuple[UUID, ...]] = set()
    candidates: list[RouteCandidate] = []

    for segment in sorted_segments:
        if not _is_initial_segment(segment, criteria.travel_date, endpoint_scope):
            continue

        path = (segment,)
        if _is_route_duration_exceeded(path, constraints):
            continue

        _append_complete_candidate(
            path=path,
            endpoint_scope=endpoint_scope,
            candidate_keys=candidate_keys,
            candidates=candidates,
        )
        if _is_complete_path(path, endpoint_scope):
            continue

        if constraints.max_transfers == 0:
            continue

        initial_visited_location_ids = _build_initial_visited_location_ids(segment)
        initial_visited_city_keys = _build_initial_visited_city_keys(segment)
        _extend_path(
            path=path,
            endpoint_scope=endpoint_scope,
            indexes=indexes,
            constraints=constraints,
            candidate_keys=candidate_keys,
            candidates=candidates,
            visited_location_ids=initial_visited_location_ids,
            visited_city_keys=initial_visited_city_keys,
        )

    return sorted(candidates, key=_candidate_sort_key)


def resolve_location_city_key(location: Location) -> UUID | None:
    if location.location_type == LocationType.city:
        return location.id
    return location.parent_location_id


def _build_indexes(segments: Sequence[RouteSegment]) -> _PlannerIndexes:
    by_origin_location_id: defaultdict[UUID, list[RouteSegment]] = defaultdict(list)
    by_origin_city_key: defaultdict[UUID, list[RouteSegment]] = defaultdict(list)

    for segment in segments:
        by_origin_location_id[segment.origin_location_id].append(segment)
        origin_city_key = resolve_location_city_key(segment.origin_location)
        if origin_city_key is not None:
            by_origin_city_key[origin_city_key].append(segment)

    return _PlannerIndexes(
        by_origin_location_id={
            location_id: tuple(values)
            for location_id, values in by_origin_location_id.items()
        },
        by_origin_city_key={
            city_key: tuple(values) for city_key, values in by_origin_city_key.items()
        },
    )


def _is_initial_segment(
    segment: RouteSegment,
    travel_date: date,
    endpoint_scope: EndpointScope,
) -> bool:
    return (
        segment.origin_location_id in endpoint_scope.origin_endpoint_ids
        and segment.departure_at.date() == travel_date
    )


def _extend_path(
    *,
    path: tuple[RouteSegment, ...],
    endpoint_scope: EndpointScope,
    indexes: _PlannerIndexes,
    constraints: PlannerConstraints,
    candidate_keys: set[tuple[UUID, ...]],
    candidates: list[RouteCandidate],
    visited_location_ids: frozenset[UUID],
    visited_city_keys: frozenset[UUID],
) -> None:
    if len(path) - 1 >= constraints.max_transfers:
        return

    used_segment_ids = frozenset(segment.id for segment in path)
    last_segment = path[-1]

    for next_segment in _iter_next_segments(last_segment, indexes):
        if next_segment.id in used_segment_ids:
            continue
        if not _is_valid_layover(path[-1], next_segment, constraints):
            continue
        if _revisits_location(next_segment, visited_location_ids):
            continue

        destination_city_key = resolve_location_city_key(
            next_segment.destination_location
        )
        if _revisits_city(destination_city_key, visited_city_keys):
            continue

        next_path = path + (next_segment,)
        if _is_route_duration_exceeded(next_path, constraints):
            continue

        _append_complete_candidate(
            path=next_path,
            endpoint_scope=endpoint_scope,
            candidate_keys=candidate_keys,
            candidates=candidates,
        )
        if _is_complete_path(next_path, endpoint_scope):
            continue

        _extend_path(
            path=next_path,
            endpoint_scope=endpoint_scope,
            indexes=indexes,
            constraints=constraints,
            candidate_keys=candidate_keys,
            candidates=candidates,
            visited_location_ids=_extend_visited_location_ids(
                visited_location_ids=visited_location_ids,
                destination_location_id=next_segment.destination_location_id,
            ),
            visited_city_keys=_extend_visited_city_keys(
                visited_city_keys=visited_city_keys,
                destination_city_key=destination_city_key,
            ),
        )


def _iter_next_segments(
    segment: RouteSegment,
    indexes: _PlannerIndexes,
) -> Iterable[RouteSegment]:
    candidates: list[RouteSegment] = list(
        indexes.by_origin_location_id.get(segment.destination_location_id, ())
    )
    transfer_city_key = resolve_location_city_key(segment.destination_location)
    if transfer_city_key is not None:
        candidates.extend(indexes.by_origin_city_key.get(transfer_city_key, ()))

    seen_segment_ids: set[UUID] = set()
    for candidate in sorted(candidates, key=_segment_sort_key):
        if candidate.id in seen_segment_ids:
            continue
        seen_segment_ids.add(candidate.id)
        if _shares_transfer_scope(
            segment.destination_location,
            candidate.origin_location,
        ):
            yield candidate


def _shares_transfer_scope(
    previous_destination: Location,
    next_origin: Location,
) -> bool:
    if previous_destination.id == next_origin.id:
        return True

    previous_city_key = resolve_location_city_key(previous_destination)
    next_city_key = resolve_location_city_key(next_origin)
    return previous_city_key is not None and previous_city_key == next_city_key


def _is_valid_layover(
    previous_segment: RouteSegment,
    next_segment: RouteSegment,
    constraints: PlannerConstraints,
) -> bool:
    layover_minutes = int(
        (next_segment.departure_at - previous_segment.arrival_at).total_seconds() // 60
    )
    return (
        constraints.min_layover_minutes
        <= layover_minutes
        <= constraints.max_layover_minutes
    )


def _is_route_duration_exceeded(
    path: Sequence[RouteSegment],
    constraints: PlannerConstraints,
) -> bool:
    if constraints.max_duration_minutes is None:
        return False
    return _resolve_total_duration_minutes(path) > constraints.max_duration_minutes


def _revisits_location(
    next_segment: RouteSegment,
    visited_location_ids: frozenset[UUID],
) -> bool:
    return next_segment.destination_location_id in visited_location_ids


def _revisits_city(
    destination_city_key: UUID | None,
    visited_city_keys: frozenset[UUID],
) -> bool:
    return (
        destination_city_key is not None and destination_city_key in visited_city_keys
    )


def _append_complete_candidate(
    *,
    path: Sequence[RouteSegment],
    endpoint_scope: EndpointScope,
    candidate_keys: set[tuple[UUID, ...]],
    candidates: list[RouteCandidate],
) -> None:
    if path[-1].destination_location_id not in endpoint_scope.destination_endpoint_ids:
        return

    segment_ids = tuple(segment.id for segment in path)
    if segment_ids in candidate_keys:
        return

    candidate_keys.add(segment_ids)
    candidates.append(_build_candidate(path, segment_ids))


def _is_complete_path(
    path: Sequence[RouteSegment],
    endpoint_scope: EndpointScope,
) -> bool:
    return path[-1].destination_location_id in endpoint_scope.destination_endpoint_ids


def _build_initial_visited_location_ids(
    segment: RouteSegment,
) -> frozenset[UUID]:
    return frozenset((segment.origin_location_id, segment.destination_location_id))


def _build_initial_visited_city_keys(segment: RouteSegment) -> frozenset[UUID]:
    return frozenset(
        city_key
        for city_key in (
            resolve_location_city_key(segment.origin_location),
            resolve_location_city_key(segment.destination_location),
        )
        if city_key is not None
    )


def _extend_visited_city_keys(
    *,
    visited_city_keys: frozenset[UUID],
    destination_city_key: UUID | None,
) -> frozenset[UUID]:
    if destination_city_key is None:
        return visited_city_keys
    return frozenset((*visited_city_keys, destination_city_key))


def _extend_visited_location_ids(
    *,
    visited_location_ids: frozenset[UUID],
    destination_location_id: UUID,
) -> frozenset[UUID]:
    return frozenset((*visited_location_ids, destination_location_id))


def _build_candidate(
    path: Sequence[RouteSegment],
    segment_ids: tuple[UUID, ...],
) -> RouteCandidate:
    return RouteCandidate(
        source="database",
        segment_ids=segment_ids,
        total_price=_resolve_total_price(path),
        total_duration_minutes=_resolve_total_duration_minutes(path),
        transfers=len(path) - 1,
        resolved_segments=tuple(path),
    )


def _resolve_total_price(path: Sequence[RouteSegment]) -> Decimal:
    return sum((segment.price_amount for segment in path), start=Decimal("0"))


def _resolve_total_duration_minutes(path: Sequence[RouteSegment]) -> int:
    return int((path[-1].arrival_at - path[0].departure_at).total_seconds() // 60)


def _segment_sort_key(segment: RouteSegment) -> tuple[object, ...]:
    return (
        segment.departure_at,
        segment.arrival_at,
        str(segment.id),
    )


def _candidate_sort_key(candidate: RouteCandidate) -> tuple[object, ...]:
    segment_ids = tuple(str(segment_id) for segment_id in candidate.segment_ids)
    return (
        candidate.total_duration_minutes
        if candidate.total_duration_minutes is not None
        else 0,
        candidate.total_price if candidate.total_price is not None else Decimal("0"),
        segment_ids,
    )


__all__ = [
    "EndpointScope",
    "MAX_TRANSFER_COUNT",
    "PlannerConstraints",
    "build_database_route_candidates",
    "build_planner_constraints",
    "resolve_location_city_key",
    "resolve_transfer_cap",
]
