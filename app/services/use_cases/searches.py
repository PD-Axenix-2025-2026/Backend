from __future__ import annotations

from collections.abc import Sequence
from typing import Protocol
from uuid import UUID, uuid4

from app.core.config import Settings
from app.models.route_segment import RouteSegment
from app.services.models import (
    RouteCandidate,
    RouteSearchCriteria,
    RouteSnapshot,
    SearchHandle,
    SearchRecord,
    SearchResultsPage,
    SearchResultsQuery,
)
from app.services.ports import (
    RouteSearchPort,
    RouteSegmentReadPort,
    SearchStateStorePort,
)
from app.services.search_results import (
    build_effective_results_query,
    build_results_page,
    build_route_list_views,
    collect_visible_routes,
)
from app.services.search_service_helpers import (
    build_search_expiration,
    build_search_handle,
)
from app.services.search_service_logging import (
    log_results_prepared,
    log_results_requested,
    log_search_created,
)
from app.services.search_snapshot_builder import (
    build_route_snapshot,
    resolve_candidate_segments,
)
from app.services.search_validation import SearchCriteriaValidator


class CreateSearchUseCase:
    def __init__(
        self,
        settings: Settings,
        validator: SearchCriteriaValidator,
        search_state_store: SearchStateStorePort,
        runtime_coordinator: SearchRuntimeCoordinatorProtocol,
    ) -> None:
        self._settings = settings
        self._validator = validator
        self._search_state_store = search_state_store
        self._runtime_coordinator = runtime_coordinator

    async def execute(self, criteria: RouteSearchCriteria) -> SearchHandle:
        search_id = uuid4()
        await self._validator.validate(criteria)

        expires_at = build_search_expiration(self._settings)
        await self._search_state_store.create_search(
            search_id=search_id,
            criteria=criteria,
            expires_at=expires_at,
        )
        log_search_created(criteria=criteria, search_id=search_id)
        self._runtime_coordinator.dispatch(search_id=search_id, criteria=criteria)
        return build_search_handle(
            self._settings,
            search_id=search_id,
            expires_at=expires_at,
        )


class GetSearchResultsUseCase:
    def __init__(self, search_state_store: SearchStateStorePort) -> None:
        self._search_state_store = search_state_store

    async def execute(
        self,
        search_id: UUID,
        query: SearchResultsQuery,
    ) -> SearchResultsPage:
        log_results_requested(search_id=search_id, query=query)
        record = await self._search_state_store.get_search(search_id)
        page = _build_results_page(record=record, query=query)
        log_results_prepared(search_id=search_id, page=page)
        return page


class RunSearchUseCase:
    def __init__(
        self,
        route_search_port: RouteSearchPort,
        route_segment_reader: RouteSegmentReadPort,
        search_state_store: SearchStateStorePort,
    ) -> None:
        self._route_search_port = route_search_port
        self._route_segment_reader = route_segment_reader
        self._search_state_store = search_state_store

    async def execute(
        self,
        *,
        search_id: UUID,
        criteria: RouteSearchCriteria,
    ) -> list[RouteSnapshot]:
        candidates = await self._route_search_port.search(criteria)
        segments_by_id = await self._load_segments_by_id(candidates)
        routes = _build_route_snapshots(
            search_id=search_id,
            candidates=candidates,
            segments_by_id=segments_by_id,
        )
        await self._search_state_store.mark_complete(search_id=search_id, routes=routes)
        return routes

    async def _load_segments_by_id(
        self,
        candidates: Sequence[RouteCandidate],
    ) -> dict[UUID, RouteSegment]:
        segment_ids = _collect_segment_ids(candidates)
        segments = await self._route_segment_reader.list_by_ids(segment_ids)
        return {segment.id: segment for segment in segments}


class SearchRuntimeCoordinatorProtocol(Protocol):
    def dispatch(
        self,
        *,
        search_id: UUID,
        criteria: RouteSearchCriteria,
    ) -> None:
        raise NotImplementedError


def _build_results_page(
    *,
    record: SearchRecord,
    query: SearchResultsQuery,
) -> SearchResultsPage:
    effective_query = build_effective_results_query(record.criteria, query)
    visible_routes = collect_visible_routes(record.routes, effective_query)
    route_views = build_route_list_views(
        visible_routes,
        sort=effective_query.sort,
    )
    return build_results_page(
        record=record,
        routes=visible_routes,
        route_views=route_views,
        query=query,
    )


def _build_route_snapshots(
    *,
    search_id: UUID,
    candidates: Sequence[RouteCandidate],
    segments_by_id: dict[UUID, RouteSegment],
) -> list[RouteSnapshot]:
    routes: list[RouteSnapshot] = []
    for candidate in candidates:
        segments = resolve_candidate_segments(
            candidate,
            segments_by_id=segments_by_id,
        )
        if segments is None:
            continue
        routes.append(
            build_route_snapshot(
                search_id=search_id,
                candidate=candidate,
                segments=segments,
            )
        )
    return routes


def _collect_segment_ids(candidates: Sequence[RouteCandidate]) -> tuple[UUID, ...]:
    return tuple(
        dict.fromkeys(
            segment_id
            for candidate in candidates
            if not candidate.resolved_segments
            for segment_id in candidate.segment_ids
        )
    )


__all__ = [
    "CreateSearchUseCase",
    "GetSearchResultsUseCase",
    "RunSearchUseCase",
]
