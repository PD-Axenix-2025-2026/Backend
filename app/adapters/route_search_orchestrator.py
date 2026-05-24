import asyncio
import logging
from collections.abc import Iterable, Sequence
from datetime import datetime
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
    deduped: list[RouteCandidate] = []
    seen_keys: set[tuple[tuple[object, ...], ...]] = set()

    for candidate in candidates:
        dedupe_key = _build_candidate_dedupe_key(candidate)
        if dedupe_key is None:
            deduped.append(candidate)
            continue
        if dedupe_key in seen_keys:
            continue
        seen_keys.add(dedupe_key)
        deduped.append(candidate)

    return deduped


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
        origin_code = segment.origin_location.code
        origin_label = segment.origin_location.name
        destination_code = segment.destination_location.code
        destination_label = segment.destination_location.name
        departure_at = segment.departure_at
        arrival_at = segment.arrival_at
        transport_type = segment.transport_type
        carrier_code = segment.carrier_code
        carrier_name = segment.carrier_name
        segment_code = segment.segment_code
    else:
        origin_code = segment.origin_location.code
        origin_label = segment.origin_location.name
        destination_code = segment.destination_location.code
        destination_label = segment.destination_location.name
        departure_at = segment.departure_at
        arrival_at = segment.arrival_at
        transport_type = segment.transport_type
        carrier_code = segment.carrier.code
        carrier_name = segment.carrier.name
        segment_code = segment.segment_code

    return (
        origin_code or origin_label,
        destination_code or destination_label,
        _normalize_datetime(departure_at),
        _normalize_datetime(arrival_at),
        transport_type.value,
        carrier_code or carrier_name,
        segment_code,
    )


def _normalize_datetime(value: datetime) -> str:
    return value.isoformat()


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
