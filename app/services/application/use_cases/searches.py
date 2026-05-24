from __future__ import annotations

from collections.abc import AsyncIterator, Callable, Sequence
from typing import Protocol, cast
from uuid import UUID, uuid4

from app.core.config import Settings
from app.models.route_segment import RouteSegment
from app.services.application.helpers import (
    build_search_expiration,
    build_search_handle,
)
from app.services.application.logging import (
    log_results_prepared,
    log_results_requested,
    log_search_created,
)
from app.services.application.ports import (
    RouteSearchPort,
    RouteSegmentReadPort,
    SearchResultsCachePort,
    SearchStateStorePort,
)
from app.services.search.cache import clone_routes_for_search
from app.services.search.contracts import (
    RouteCandidate,
    RouteCandidateBatch,
    RouteSearchCriteria,
    SearchResultsQuery,
    SearchStatus,
)
from app.services.search.results import (
    SearchHandle,
    SearchResultsPage,
    build_effective_results_query,
    build_results_page,
    build_route_list_views,
    collect_visible_routes,
)
from app.services.search.snapshot_builder import (
    build_route_snapshot,
    resolve_candidate_segments,
)
from app.services.search.store.models import RouteSnapshot, SearchRecord
from app.services.search.validation import SearchCriteriaValidator


class CreateSearchUseCase:
    def __init__(
        self,
        settings: Settings,
        validator: SearchCriteriaValidator,
        search_state_store: SearchStateStorePort,
        runtime_coordinator: SearchRuntimeCoordinatorProtocol,
        results_cache: SearchResultsCachePort | None = None,
    ) -> None:
        self._settings = settings
        self._validator = validator
        self._search_state_store = search_state_store
        self._runtime_coordinator = runtime_coordinator
        self._results_cache = results_cache

    async def execute(self, criteria: RouteSearchCriteria) -> SearchHandle:
        search_id = uuid4()
        await self._validator.validate(criteria)
        cached_routes = await self._load_cached_routes(criteria)

        expires_at = build_search_expiration(self._settings)
        await self._search_state_store.create_search(
            search_id=search_id,
            criteria=criteria,
            expires_at=expires_at,
        )
        log_search_created(criteria=criteria, search_id=search_id)
        if cached_routes is not None:
            await self._search_state_store.mark_complete(
                search_id=search_id,
                routes=clone_routes_for_search(
                    search_id=search_id,
                    routes=cached_routes,
                ),
            )
            return build_search_handle(
                self._settings,
                search_id=search_id,
                expires_at=expires_at,
                status=SearchStatus.complete,
            )

        self._runtime_coordinator.dispatch(search_id=search_id, criteria=criteria)
        return build_search_handle(
            self._settings,
            search_id=search_id,
            expires_at=expires_at,
        )

    async def _load_cached_routes(
        self,
        criteria: RouteSearchCriteria,
    ) -> list[RouteSnapshot] | None:
        if self._results_cache is None:
            return None
        return await self._results_cache.get(criteria)


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
        results_cache: SearchResultsCachePort | None = None,
    ) -> None:
        self._route_search_port = route_search_port
        self._route_segment_reader = route_segment_reader
        self._search_state_store = search_state_store
        self._results_cache = results_cache

    async def execute(
        self,
        *,
        search_id: UUID,
        criteria: RouteSearchCriteria,
    ) -> list[RouteSnapshot]:
        search_batches = _resolve_search_batches(self._route_search_port)
        if search_batches is not None:
            return await self._execute_progressive_search(
                search_id=search_id,
                criteria=criteria,
                search_batches=search_batches,
            )

        candidates = await self._route_search_port.search(criteria)
        routes = await self._build_route_snapshots(
            search_id=search_id,
            candidates=candidates,
        )
        await self._search_state_store.mark_complete(search_id=search_id, routes=routes)
        await self._store_cached_routes(criteria=criteria, routes=routes)
        return routes

    async def _execute_progressive_search(
        self,
        *,
        search_id: UUID,
        criteria: RouteSearchCriteria,
        search_batches: SearchBatchesCallable,
    ) -> list[RouteSnapshot]:
        final_routes: list[RouteSnapshot] = []
        async for batch in search_batches(criteria):
            routes = await self._build_route_snapshots(
                search_id=search_id,
                candidates=batch.candidates,
            )
            if batch.is_final:
                final_routes = routes
                await self._search_state_store.mark_complete(
                    search_id=search_id,
                    routes=routes,
                )
                await self._store_cached_routes(criteria=criteria, routes=routes)
            else:
                await self._search_state_store.append_routes(
                    search_id=search_id,
                    routes=routes,
                )
        return final_routes

    async def _store_cached_routes(
        self,
        *,
        criteria: RouteSearchCriteria,
        routes: list[RouteSnapshot],
    ) -> None:
        if self._results_cache is None:
            return
        await self._results_cache.set(criteria, routes)

    async def _build_route_snapshots(
        self,
        *,
        search_id: UUID,
        candidates: Sequence[RouteCandidate],
    ) -> list[RouteSnapshot]:
        segments_by_id = await self._load_segments_by_id(candidates)
        return _build_route_snapshots(
            search_id=search_id,
            candidates=candidates,
            segments_by_id=segments_by_id,
        )

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


SearchBatchesCallable = Callable[
    [RouteSearchCriteria],
    AsyncIterator[RouteCandidateBatch],
]


class RouteSearchBatchPort(Protocol):
    def search_batches(
        self,
        criteria: RouteSearchCriteria,
    ) -> AsyncIterator[RouteCandidateBatch]: ...


def _resolve_search_batches(
    route_search_port: RouteSearchPort,
) -> SearchBatchesCallable | None:
    search_batches = getattr(route_search_port, "search_batches", None)
    if not callable(search_batches):
        return None
    return cast(SearchBatchesCallable, search_batches)


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
