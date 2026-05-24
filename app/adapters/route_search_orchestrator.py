import asyncio
import logging
import re
from collections.abc import Iterable, Sequence
from datetime import datetime
from math import inf
from typing import cast
from uuid import UUID

from app.adapters.database_route_search import DatabaseRouteSearchAdapter
from app.services.application.ports import RouteSearchPort
from app.services.search.contracts import (
    ProviderRouteSegment,
    ResolvedRouteSegment,
    RouteCandidate,
    RouteSearchCriteria,
)
from app.services.search.planner import (
    build_k_shortest_route_candidates,
    build_planner_constraints,
)

logger = logging.getLogger(__name__)

_SOURCE_PRIORITY = {
    "yandex_rasp_api": 0,
    "rzd_api": 1,
    "graph_search": 2,
    "database": 3,
}

_CYRILLIC_CONFUSABLES = str.maketrans(
    {
        "\u0410": "A",
        "\u0412": "B",
        "\u0415": "E",
        "\u041a": "K",
        "\u041c": "M",
        "\u041d": "H",
        "\u041e": "O",
        "\u0420": "P",
        "\u0421": "C",
        "\u0422": "T",
        "\u0425": "X",
        "\u0430": "A",
        "\u0432": "B",
        "\u0435": "E",
        "\u043a": "K",
        "\u043c": "M",
        "\u043d": "H",
        "\u043e": "O",
        "\u0440": "P",
        "\u0441": "C",
        "\u0442": "T",
        "\u0445": "X",
    }
)


class RouteSearchOrchestratorError(Exception):
    """Ошибка оркестратора поиска маршрутов."""

    pass


class RouteSearchOrchestrator(RouteSearchPort):
    """
    Оркестратор для запуска нескольких адаптеров поиска маршрутов.

    Можно запускать все адаптеры вместе, либо выбрать подмножество
    через аргумент `adapters` в методе search().
    """

    def __init__(self, adapters: Sequence[RouteSearchPort]):
        self._adapters = list(adapters)

    async def search(
        self,
        criteria: RouteSearchCriteria,
        adapters: Iterable[RouteSearchPort] | None = None,
    ) -> list[RouteCandidate]:
        """
        Запускает поиск по всем адаптерам (или выбранным) параллельно
        и возвращает объединённый список результатов.
        """
        selected_adapters = list(adapters) if adapters is not None else self._adapters

        if not selected_adapters:
            raise Exception("No adapters passed to route search orchestrator")

        selected_adapters = _select_adapters_for_criteria(
            criteria=criteria,
            adapters=selected_adapters,
        )
        tasks = [adapter.search(criteria) for adapter in selected_adapters]
        results = await asyncio.gather(*tasks, return_exceptions=True)

        successful_results: list[tuple[RouteSearchPort, list[RouteCandidate]]] = []
        errors: list[Exception] = []

        for adapter, result in zip(selected_adapters, results, strict=True):
            if isinstance(result, Exception):
                logger.error(
                    "Route search adapter failed: %s, error=%s",
                    adapter.__class__.__name__,
                    result,
                )
                errors.append(result)
            else:
                successful_results.append((adapter, cast(list[RouteCandidate], result)))

        combined = _combine_results(
            criteria=criteria,
            successful_results=successful_results,
        )

        if not combined and errors:
            raise RouteSearchOrchestratorError("All route search adapters failed")

        return combined


def _select_adapters_for_criteria(
    *,
    criteria: RouteSearchCriteria,
    adapters: Sequence[RouteSearchPort],
) -> list[RouteSearchPort]:
    database_adapters = [
        adapter for adapter in adapters if _is_database_adapter(adapter)
    ]
    external_adapters = [
        adapter for adapter in adapters if not _is_database_adapter(adapter)
    ]
    if not external_adapters:
        return list(adapters)

    if (criteria.preferences.max_transfers or 0) <= 0:
        return external_adapters

    return [*external_adapters, *database_adapters]


def _combine_results(
    *,
    criteria: RouteSearchCriteria,
    successful_results: Sequence[tuple[RouteSearchPort, list[RouteCandidate]]],
) -> list[RouteCandidate]:
    if not successful_results:
        return []

    has_external_candidates = any(
        candidates and not _is_database_adapter(adapter)
        for adapter, candidates in successful_results
    )

    combined: list[RouteCandidate] = []
    for adapter, candidates in successful_results:
        if (
            has_external_candidates
            and _is_database_adapter(adapter)
            and (criteria.preferences.max_transfers or 0) > 0
        ):
            combined.extend(
                candidate for candidate in candidates if candidate.transfers > 0
            )
            continue

        combined.extend(candidates)

    if has_external_candidates and (criteria.preferences.max_transfers or 0) > 0:
        combined.extend(
            _build_graph_candidates(
                criteria=criteria,
                successful_results=successful_results,
            )
        )

    return _dedupe_candidates(combined)


def _is_database_adapter(adapter: RouteSearchPort) -> bool:
    return isinstance(adapter, DatabaseRouteSearchAdapter)


def _dedupe_candidates(candidates: Sequence[RouteCandidate]) -> list[RouteCandidate]:
    return _prune_dominated_transfer_candidates(
        _dedupe_exact_candidate_matches(candidates)
    )


def _dedupe_exact_candidate_matches(
    candidates: Sequence[RouteCandidate],
) -> list[RouteCandidate]:
    entries: list[
        tuple[int, tuple[tuple[object, ...], ...] | None, RouteCandidate | None]
    ] = []
    best_by_key: dict[tuple[tuple[object, ...], ...], RouteCandidate] = {}

    for index, candidate in enumerate(candidates):
        dedupe_key = _build_candidate_dedupe_key(candidate)
        if dedupe_key is None:
            entries.append((index, None, candidate))
            continue

        if dedupe_key not in best_by_key:
            entries.append((index, dedupe_key, None))
            best_by_key[dedupe_key] = candidate
        elif _is_candidate_better(candidate, best_by_key[dedupe_key]):
            best_by_key[dedupe_key] = candidate

    deduped: list[RouteCandidate] = []
    for _index, dedupe_key, entry_candidate in entries:
        if dedupe_key is None:
            if entry_candidate is not None:
                deduped.append(entry_candidate)
            continue
        deduped.append(best_by_key[dedupe_key])

    return deduped


def _prune_dominated_transfer_candidates(
    candidates: Sequence[RouteCandidate],
) -> list[RouteCandidate]:
    entries: list[
        tuple[int, tuple[tuple[object, ...], str] | None, RouteCandidate | None]
    ] = []
    best_by_tail_key: dict[tuple[tuple[object, ...], str], RouteCandidate] = {}

    for index, candidate in enumerate(candidates):
        tail_key = _build_transfer_tail_key(candidate)
        if tail_key is None:
            entries.append((index, None, candidate))
            continue

        if tail_key not in best_by_tail_key:
            entries.append((index, tail_key, None))
            best_by_tail_key[tail_key] = candidate
        elif _is_candidate_better_for_transfer_pruning(
            candidate,
            best_by_tail_key[tail_key],
        ):
            best_by_tail_key[tail_key] = candidate

    pruned: list[RouteCandidate] = []
    for _index, tail_key, entry_candidate in entries:
        if tail_key is None:
            if entry_candidate is not None:
                pruned.append(entry_candidate)
            continue
        pruned.append(best_by_tail_key[tail_key])

    return pruned


def _build_candidate_dedupe_key(
    candidate: RouteCandidate,
) -> tuple[tuple[object, ...], ...] | None:
    if not candidate.resolved_segments:
        return None
    return tuple(
        _build_segment_dedupe_key(segment) for segment in candidate.resolved_segments
    )


def _build_segment_dedupe_key(
    segment: ResolvedRouteSegment,
) -> tuple[object, ...]:
    if isinstance(segment, ProviderRouteSegment):
        origin_id = segment.origin_location.id
        destination_id = segment.destination_location.id
        departure_at = segment.departure_at
        arrival_at = segment.arrival_at
        transport_type = segment.transport_type
        segment_code = segment.segment_code
    else:
        origin_id = segment.origin_location.id
        destination_id = segment.destination_location.id
        departure_at = segment.departure_at
        arrival_at = segment.arrival_at
        transport_type = segment.transport_type
        segment_code = segment.segment_code

    return (
        origin_id,
        destination_id,
        _normalize_datetime(departure_at),
        _normalize_datetime(arrival_at),
        transport_type.value,
        _normalize_segment_code(segment_code),
    )


def _normalize_datetime(value: datetime) -> str:
    return value.isoformat()


def _normalize_segment_code(value: str | None) -> str:
    if value is None:
        return ""

    normalized = value.upper().translate(_CYRILLIC_CONFUSABLES)
    normalized = "".join(normalized.split())
    return re.sub(r"(?<=\d)[X/\\-](?=\d)", "/", normalized)


def _is_candidate_better(
    candidate: RouteCandidate,
    current: RouteCandidate,
) -> bool:
    return _candidate_quality_key(candidate) < _candidate_quality_key(current)


def _candidate_quality_key(candidate: RouteCandidate) -> tuple[object, ...]:
    return (
        candidate.total_price is None,
        -_priced_segment_count(candidate),
        _candidate_duration_minutes(candidate),
        _source_priority(candidate.source),
        tuple(str(segment_id) for segment_id in candidate.segment_ids),
    )


def _priced_segment_count(candidate: RouteCandidate) -> int:
    return sum(
        1 for segment in candidate.resolved_segments if segment.price_amount is not None
    )


def _candidate_duration_minutes(candidate: RouteCandidate) -> float:
    if candidate.total_duration_minutes is not None:
        return float(candidate.total_duration_minutes)
    if candidate.resolved_segments:
        return float(
            int(
                (
                    candidate.resolved_segments[-1].arrival_at
                    - candidate.resolved_segments[0].departure_at
                ).total_seconds()
                // 60
            )
        )
    return inf


def _source_priority(source: str) -> int:
    return _SOURCE_PRIORITY.get(source, len(_SOURCE_PRIORITY))


def _build_transfer_tail_key(
    candidate: RouteCandidate,
) -> tuple[tuple[object, ...], str] | None:
    if candidate.transfers <= 0 or len(candidate.resolved_segments) < 2:
        return None

    tail_segments = candidate.resolved_segments[1:]
    return (
        tuple(
            item
            for segment in tail_segments
            for item in _build_segment_dedupe_key(segment)
        ),
        _normalize_datetime(tail_segments[-1].arrival_at),
    )


def _is_candidate_better_for_transfer_pruning(
    candidate: RouteCandidate,
    current: RouteCandidate,
) -> bool:
    return _candidate_transfer_pruning_key(candidate) < _candidate_transfer_pruning_key(
        current
    )


def _candidate_transfer_pruning_key(candidate: RouteCandidate) -> tuple[object, ...]:
    return (
        -_candidate_departure_timestamp(candidate),
        _candidate_duration_minutes(candidate),
        _candidate_quality_key(candidate),
    )


def _candidate_departure_timestamp(candidate: RouteCandidate) -> float:
    if candidate.resolved_segments:
        return candidate.resolved_segments[0].departure_at.timestamp()
    return -inf


def _build_graph_candidates(
    *,
    criteria: RouteSearchCriteria,
    successful_results: Sequence[tuple[RouteSearchPort, list[RouteCandidate]]],
) -> list[RouteCandidate]:
    segments = _collect_resolved_segments(successful_results)
    if not segments:
        return []

    return build_k_shortest_route_candidates(
        criteria=criteria,
        segments=segments,
        constraints=build_planner_constraints(criteria),
        source="graph_search",
    )


def _collect_resolved_segments(
    successful_results: Sequence[tuple[RouteSearchPort, list[RouteCandidate]]],
) -> list[ResolvedRouteSegment]:
    segments_by_id: dict[UUID, ResolvedRouteSegment] = {}
    for _, candidates in successful_results:
        for candidate in candidates:
            for segment in candidate.resolved_segments:
                segments_by_id[_segment_id(segment)] = segment
    return list(segments_by_id.values())


def _segment_id(segment: ResolvedRouteSegment) -> UUID:
    if isinstance(segment, ProviderRouteSegment):
        return segment.segment_id
    return segment.id
