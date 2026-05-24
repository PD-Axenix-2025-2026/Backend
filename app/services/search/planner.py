from __future__ import annotations

from collections import defaultdict
from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from datetime import date
from decimal import Decimal
from itertools import islice
from uuid import UUID

import networkx as nx  # type: ignore[import-untyped]

from app.models.enums import LocationType
from app.models.location import Location
from app.models.route_segment import RouteSegment
from app.services.search.contracts import (
    ResolvedRouteSegment,
    RouteCandidate,
    RouteSearchCriteria,
    SearchSortOption,
)

MIN_LAYOVER_MINUTES = 60
MAX_LAYOVER_MINUTES = 720
MAX_TRANSFER_COUNT = 3
MAX_SHORTEST_PATHS = 50
TRANSFER_PENALTY_MINUTES = 60.0
UNKNOWN_PRICE_PENALTY = 1_000_000.0
BEST_DURATION_WEIGHT = 1.0
BEST_PRICE_WEIGHT = 0.1
DURATION_WEIGHT = 1.0
PRICE_WEIGHT = 1.0


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


@dataclass(slots=True, frozen=True)
class RouteWeightProfile:
    duration_weight: float
    price_weight: float
    transfer_penalty: float


@dataclass(slots=True)
class _GraphIndexes:
    by_origin_location_id: dict[UUID, tuple[ResolvedRouteSegment, ...]]
    by_origin_city_key: dict[UUID, tuple[ResolvedRouteSegment, ...]]


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
    return build_k_shortest_route_candidates(
        criteria=criteria,
        segments=segments,
        endpoint_scope=endpoint_scope,
        constraints=constraints,
        source="database",
    )


def build_k_shortest_route_candidates(
    *,
    criteria: RouteSearchCriteria,
    segments: Sequence[ResolvedRouteSegment],
    constraints: PlannerConstraints,
    source: str,
    endpoint_scope: EndpointScope | None = None,
    max_candidates: int = MAX_SHORTEST_PATHS,
    weight_profiles: Sequence[RouteWeightProfile] | None = None,
) -> list[RouteCandidate]:
    if not segments:
        return []

    filtered_segments = _filter_segments_by_transport_types(criteria, segments)
    if not filtered_segments:
        return []

    resolved_endpoint_scope = endpoint_scope or _build_endpoint_scope_from_segments(
        criteria=criteria,
        segments=filtered_segments,
    )
    if (
        not resolved_endpoint_scope.origin_endpoint_ids
        or not resolved_endpoint_scope.destination_endpoint_ids
    ):
        return []

    profiles = weight_profiles or _build_default_weight_profiles()
    candidate_keys: set[tuple[UUID, ...]] = set()
    candidates: list[RouteCandidate] = []

    for profile in profiles:
        graph_context = _build_segment_graph(
            segments=filtered_segments,
            criteria=criteria,
            endpoint_scope=resolved_endpoint_scope,
            constraints=constraints,
            weight_profile=profile,
        )
        candidates.extend(
            _collect_graph_candidates(
                graph_context=graph_context,
                constraints=constraints,
                source=source,
                candidate_keys=candidate_keys,
                max_candidates=max_candidates,
            )
        )
    return candidates
    # return sorted(candidates, key=_candidate_sort_key)


def get_sort_weight_profile(sort: SearchSortOption) -> RouteWeightProfile:
    if sort == SearchSortOption.duration:
        return RouteWeightProfile(
            duration_weight=DURATION_WEIGHT,
            price_weight=0.0,
            transfer_penalty=0.0,
        )
    elif sort == SearchSortOption.price:
        return RouteWeightProfile(
            duration_weight=0.0,
            price_weight=PRICE_WEIGHT,
            transfer_penalty=0.0,
        )
    else:
        return RouteWeightProfile(
            duration_weight=BEST_DURATION_WEIGHT,
            price_weight=BEST_PRICE_WEIGHT,
            transfer_penalty=TRANSFER_PENALTY_MINUTES,
        )


def _build_default_weight_profiles() -> tuple[RouteWeightProfile, ...]:
    return (
        get_sort_weight_profile(SearchSortOption.duration),
        get_sort_weight_profile(SearchSortOption.price),
        get_sort_weight_profile(SearchSortOption.best),
    )


def _filter_segments_by_transport_types(
    criteria: RouteSearchCriteria,
    segments: Sequence[ResolvedRouteSegment],
) -> list[ResolvedRouteSegment]:
    if not criteria.transport_types:
        return list(segments)
    allowed = set(criteria.transport_types)
    return [segment for segment in segments if segment.transport_type in allowed]


def _build_endpoint_scope_from_segments(
    *,
    criteria: RouteSearchCriteria,
    segments: Sequence[ResolvedRouteSegment],
) -> EndpointScope:
    origin_endpoint_ids: set[UUID] = {criteria.origin_id}
    destination_endpoint_ids: set[UUID] = {criteria.destination_id}

    for segment in segments:
        origin_location = segment.origin_location
        destination_location = segment.destination_location
        if _matches_endpoint(
            endpoint_id=criteria.origin_id,
            endpoint_type=criteria.origin_type,
            location=origin_location,
        ):
            origin_endpoint_ids.add(origin_location.id)
        if _matches_endpoint(
            endpoint_id=criteria.destination_id,
            endpoint_type=criteria.destination_type,
            location=destination_location,
        ):
            destination_endpoint_ids.add(destination_location.id)

    return EndpointScope(
        origin_endpoint_ids=frozenset(origin_endpoint_ids),
        destination_endpoint_ids=frozenset(destination_endpoint_ids),
    )


def _matches_endpoint(
    *,
    endpoint_id: UUID,
    endpoint_type: LocationType,
    location: Location,
) -> bool:
    if location.id == endpoint_id:
        return True
    return (
        endpoint_type == LocationType.city
        and location.parent_location_id == endpoint_id
    )


@dataclass(slots=True)
class _GraphContext:
    graph: nx.DiGraph
    source_node: str
    target_node: str
    segment_by_id: dict[UUID, ResolvedRouteSegment]


def _build_segment_graph(
    *,
    segments: Sequence[ResolvedRouteSegment],
    criteria: RouteSearchCriteria,
    endpoint_scope: EndpointScope,
    constraints: PlannerConstraints,
    weight_profile: RouteWeightProfile,
) -> _GraphContext:
    graph = nx.DiGraph()
    source_node = "_source"
    target_node = "_target"
    graph.add_node(source_node)
    graph.add_node(target_node)

    sorted_segments = tuple(sorted(segments, key=_segment_sort_key))
    indexes = _build_graph_indexes(sorted_segments)
    segment_by_id = {_segment_node_id(segment): segment for segment in sorted_segments}

    for segment in sorted_segments:
        segment_node = _segment_node_id(segment)
        if _is_initial_graph_segment(segment, criteria.travel_date, endpoint_scope):
            graph.add_edge(
                source_node,
                segment_node,
                weight=_resolve_segment_weight(segment, weight_profile),
            )

        if _is_complete_graph_segment(segment, endpoint_scope):
            graph.add_edge(segment_node, target_node, weight=0.0)

        if constraints.max_transfers == 0:
            continue

        for next_segment in _iter_next_segments_for_graph(segment, indexes):
            if _segment_node_id(next_segment) == segment_node:
                continue
            if not _is_valid_layover(segment, next_segment, constraints):
                continue
            graph.add_edge(
                segment_node,
                _segment_node_id(next_segment),
                weight=_resolve_segment_weight(next_segment, weight_profile)
                + weight_profile.transfer_penalty,
            )

    return _GraphContext(
        graph=graph,
        source_node=source_node,
        target_node=target_node,
        segment_by_id=segment_by_id,
    )


def _build_graph_indexes(
    segments: Sequence[ResolvedRouteSegment],
) -> _GraphIndexes:
    by_origin_location_id: defaultdict[UUID, list[ResolvedRouteSegment]] = defaultdict(
        list
    )
    by_origin_city_key: defaultdict[UUID, list[ResolvedRouteSegment]] = defaultdict(
        list
    )

    for segment in segments:
        by_origin_location_id[segment.origin_location.id].append(segment)
        origin_city_key = resolve_location_city_key(segment.origin_location)
        if origin_city_key is not None:
            by_origin_city_key[origin_city_key].append(segment)

    return _GraphIndexes(
        by_origin_location_id={
            location_id: tuple(values)
            for location_id, values in by_origin_location_id.items()
        },
        by_origin_city_key={
            city_key: tuple(values) for city_key, values in by_origin_city_key.items()
        },
    )


def _segment_node_id(segment: ResolvedRouteSegment) -> UUID:
    if isinstance(segment, RouteSegment):
        return segment.id
    return segment.segment_id


def _is_initial_graph_segment(
    segment: ResolvedRouteSegment,
    travel_date: date,
    endpoint_scope: EndpointScope,
) -> bool:
    return (
        segment.origin_location.id in endpoint_scope.origin_endpoint_ids
        and segment.departure_at.date() == travel_date
    )


def _is_complete_graph_segment(
    segment: ResolvedRouteSegment,
    endpoint_scope: EndpointScope,
) -> bool:
    return segment.destination_location.id in endpoint_scope.destination_endpoint_ids


def _iter_next_segments_for_graph(
    segment: ResolvedRouteSegment,
    indexes: _GraphIndexes,
) -> Iterable[ResolvedRouteSegment]:
    candidates: list[ResolvedRouteSegment] = list(
        indexes.by_origin_location_id.get(segment.destination_location.id, ())
    )
    transfer_city_key = resolve_location_city_key(segment.destination_location)
    if transfer_city_key is not None:
        candidates.extend(indexes.by_origin_city_key.get(transfer_city_key, ()))

    seen_segment_ids: set[UUID] = set()
    for candidate in sorted(candidates, key=_segment_sort_key):
        candidate_id = _segment_node_id(candidate)
        if candidate_id in seen_segment_ids:
            continue
        seen_segment_ids.add(candidate_id)
        if _shares_transfer_scope(
            segment.destination_location,
            candidate.origin_location,
        ):
            yield candidate


def _resolve_segment_weight(
    segment: ResolvedRouteSegment,
    profile: RouteWeightProfile,
) -> float:
    duration_score = segment.duration_minutes * profile.duration_weight
    if profile.price_weight == 0.0:
        price_score = 0.0
    else:
        price_amount = segment.price_amount
        price_score = (
            UNKNOWN_PRICE_PENALTY if price_amount is None else float(price_amount)
        ) * profile.price_weight
    return float(duration_score + price_score)


def _collect_graph_candidates(
    *,
    graph_context: _GraphContext,
    constraints: PlannerConstraints,
    source: str,
    candidate_keys: set[tuple[UUID, ...]],
    max_candidates: int,
) -> list[RouteCandidate]:
    if graph_context.source_node not in graph_context.graph:
        return []

    candidates: list[RouteCandidate] = []
    try:
        paths = nx.shortest_simple_paths(
            graph_context.graph,
            graph_context.source_node,
            graph_context.target_node,
            weight="weight",
        )
        for path in islice(paths, max_candidates * 5):
            segment_ids = path[1:-1]
            if not segment_ids:
                continue
            resolved_segments = [
                graph_context.segment_by_id[segment_id] for segment_id in segment_ids
            ]
            if not _is_path_valid(resolved_segments, constraints):
                continue
            path_key = tuple(_segment_node_id(segment) for segment in resolved_segments)
            if path_key in candidate_keys:
                continue
            candidate_keys.add(path_key)
            candidates.append(_build_graph_candidate(resolved_segments, source))
            if len(candidates) >= max_candidates:
                break
    except nx.NetworkXNoPath:
        return []

    return candidates


def _is_path_valid(
    path: Sequence[ResolvedRouteSegment],
    constraints: PlannerConstraints,
) -> bool:
    if len(path) - 1 > constraints.max_transfers:
        return False
    if _is_route_duration_exceeded(path, constraints):
        return False
    return not _path_has_cycle(path)


def _path_has_cycle(path: Sequence[ResolvedRouteSegment]) -> bool:
    visited_location_ids: set[UUID] = set()
    visited_city_keys: set[UUID] = set()

    for index, segment in enumerate(path):
        if index == 0:
            visited_location_ids.update(
                (segment.origin_location.id, segment.destination_location.id)
            )
            visited_city_keys.update(
                key
                for key in (
                    resolve_location_city_key(segment.origin_location),
                    resolve_location_city_key(segment.destination_location),
                )
                if key is not None
            )
            continue

        destination_id = segment.destination_location.id
        if destination_id in visited_location_ids:
            return True
        destination_city_key = resolve_location_city_key(segment.destination_location)
        if (
            destination_city_key is not None
            and destination_city_key in visited_city_keys
        ):
            return True
        visited_location_ids.add(destination_id)
        if destination_city_key is not None:
            visited_city_keys.add(destination_city_key)

    return False


def _build_graph_candidate(
    path: Sequence[ResolvedRouteSegment],
    source: str,
) -> RouteCandidate:
    segment_ids = tuple(_segment_node_id(segment) for segment in path)
    return RouteCandidate(
        source=source,
        segment_ids=segment_ids,
        total_price=_resolve_total_price(path),
        total_duration_minutes=_resolve_total_duration_minutes(path),
        transfers=len(path) - 1,
        resolved_segments=tuple(path),
    )


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
    previous_segment: ResolvedRouteSegment,
    next_segment: ResolvedRouteSegment,
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
    path: Sequence[ResolvedRouteSegment],
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


def _resolve_total_price(path: Sequence[ResolvedRouteSegment]) -> Decimal | None:
    total = Decimal("0")
    for segment in path:
        if segment.price_amount is None:
            return None
        total += segment.price_amount
    return total


def _resolve_total_duration_minutes(path: Sequence[ResolvedRouteSegment]) -> int:
    return int((path[-1].arrival_at - path[0].departure_at).total_seconds() // 60)


def _segment_sort_key(segment: ResolvedRouteSegment) -> tuple[object, ...]:
    return (
        segment.departure_at,
        segment.arrival_at,
        str(_segment_node_id(segment)),
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
    "build_k_shortest_route_candidates",
    "build_planner_constraints",
    "resolve_location_city_key",
    "resolve_transfer_cap",
]
