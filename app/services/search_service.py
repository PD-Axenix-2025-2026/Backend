from __future__ import annotations

import asyncio
from collections.abc import Callable, Sequence
from datetime import timedelta
from typing import Protocol
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit
from uuid import UUID, uuid4

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.core.config import Settings
from app.models.location import Location
from app.models.route_segment import RouteSegment
from app.services.contracts import (
    RouteCandidate,
    RouteSearchCriteria,
    SearchResultsQuery,
    SearchStatus,
)
from app.services.route_aggregation import RouteAggregationService
from app.services.search_results import (
    CheckoutLinkInfo,
    DecimalRange,
    EffectiveResultsQuery,
    IntegerRange,
    RouteListView,
    SearchHandle,
    SearchResultsPage,
    TransferFacet,
    TransportTypeFacet,
    build_effective_results_query,
    build_results_page,
    build_route_list_views,
    collect_visible_routes,
)
from app.services.search_snapshot_builder import (
    build_route_snapshot,
    resolve_candidate_segments,
)
from app.services.search_store import (
    InMemorySearchStore,
    RouteNotFoundError,
    RouteSnapshot,
    SearchNotFoundError,
    utc_now,
)


class SearchValidationError(ValueError):
    pass


class LocationRepositoryProtocol(Protocol):
    async def get_by_id(self, location_id: UUID) -> Location | None: ...


class RouteSegmentRepositoryProtocol(Protocol):
    async def list_by_ids(self, segment_ids: Sequence[UUID]) -> list[RouteSegment]: ...


LocationRepositoryFactory = Callable[[AsyncSession], LocationRepositoryProtocol]
RouteSegmentRepositoryFactory = Callable[[AsyncSession], RouteSegmentRepositoryProtocol]
RouteAggregationFactory = Callable[[AsyncSession], RouteAggregationService]


class SearchService:
    def __init__(
        self,
        settings: Settings,
        session_factory: async_sessionmaker[AsyncSession],
        search_store: InMemorySearchStore,
        location_repository_factory: LocationRepositoryFactory,
        route_segment_repository_factory: RouteSegmentRepositoryFactory,
        route_aggregation_factory: RouteAggregationFactory,
    ) -> None:
        self._settings = settings
        self._session_factory = session_factory
        self._search_store = search_store
        self._location_repository_factory = location_repository_factory
        self._route_segment_repository_factory = route_segment_repository_factory
        self._route_aggregation_factory = route_aggregation_factory
        self._tasks: set[asyncio.Task[None]] = set()

    async def create_search(self, criteria: RouteSearchCriteria) -> SearchHandle:
        await self._validate_criteria(criteria)

        search_id = uuid4()
        expires_at = utc_now() + timedelta(seconds=self._settings.search_ttl_seconds)
        await self._search_store.create_search(
            search_id=search_id,
            criteria=criteria,
            expires_at=expires_at,
        )
        self._start_background_search(search_id=search_id, criteria=criteria)

        return SearchHandle(
            search_id=search_id,
            status=SearchStatus.pending,
            results_url=f"{self._settings.api_prefix}/searches/{search_id}/results",
            poll_after_ms=self._settings.search_poll_after_ms,
            expires_at=expires_at,
        )

    async def get_results(
        self,
        search_id: UUID,
        query: SearchResultsQuery,
    ) -> SearchResultsPage:
        record = await self._search_store.get_search(search_id)
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

    async def get_route_detail(self, route_id: UUID) -> RouteSnapshot:
        _record, route = await self._search_store.get_route(route_id)
        return route

    async def build_checkout_link(
        self,
        route_id: UUID,
        provider_offer_id: str | None = None,
    ) -> CheckoutLinkInfo:
        record, route = await self._search_store.get_route(route_id)
        expires_at = min(
            record.expires_at,
            utc_now() + timedelta(seconds=self._settings.checkout_link_ttl_seconds),
        )
        return CheckoutLinkInfo(
            method="GET",
            url=self._build_checkout_url(
                route_id=route.route_id,
                search_id=record.search_id,
                provider_offer_id=provider_offer_id,
            ),
            expires_at=expires_at,
        )

    async def shutdown(self) -> None:
        tasks = tuple(self._tasks)
        for task in tasks:
            task.cancel()
        if tasks:
            await asyncio.gather(*tasks, return_exceptions=True)

    async def _validate_criteria(self, criteria: RouteSearchCriteria) -> None:
        if criteria.origin_id == criteria.destination_id:
            raise SearchValidationError("Origin and destination must be different")
        if criteria.passengers.adults < 1:
            raise SearchValidationError("At least one adult passenger is required")
        if criteria.passengers.total <= 0:
            raise SearchValidationError("At least one passenger is required")

        async with self._session_factory() as session:
            location_repository = self._location_repository_factory(session)
            origin = await location_repository.get_by_id(criteria.origin_id)
            destination = await location_repository.get_by_id(criteria.destination_id)

        if origin is None or destination is None:
            raise SearchValidationError("Selected locations were not found")
        if origin.location_type != criteria.origin_type:
            raise SearchValidationError("Origin location type does not match")
        if destination.location_type != criteria.destination_type:
            raise SearchValidationError("Destination location type does not match")

    def _start_background_search(
        self,
        *,
        search_id: UUID,
        criteria: RouteSearchCriteria,
    ) -> None:
        task = asyncio.create_task(
            self._run_search(search_id=search_id, criteria=criteria),
            name=f"route-search-{search_id}",
        )
        self._tasks.add(task)
        task.add_done_callback(self._tasks.discard)

    async def _run_search(
        self,
        *,
        search_id: UUID,
        criteria: RouteSearchCriteria,
    ) -> None:
        try:
            routes = await self._load_route_snapshots(
                search_id=search_id,
                criteria=criteria,
            )
            await self._search_store.mark_complete(search_id=search_id, routes=routes)
        except asyncio.CancelledError:
            raise
        except (SearchNotFoundError, RouteNotFoundError):
            return
        except Exception as exc:
            try:
                await self._search_store.mark_failed(
                    search_id=search_id,
                    error_message=str(exc),
                )
            except SearchNotFoundError:
                return

    async def _load_route_snapshots(
        self,
        *,
        search_id: UUID,
        criteria: RouteSearchCriteria,
    ) -> list[RouteSnapshot]:
        async with self._session_factory() as session:
            candidates = await self._search_candidates(session, criteria)
            segments_by_id = await self._load_segments_by_id(session, candidates)

        return self._build_route_snapshots(
            search_id=search_id,
            candidates=candidates,
            segments_by_id=segments_by_id,
        )

    async def _search_candidates(
        self,
        session: AsyncSession,
        criteria: RouteSearchCriteria,
    ) -> list[RouteCandidate]:
        route_service = self._route_aggregation_factory(session)
        return await route_service.search(criteria)

    async def _load_segments_by_id(
        self,
        session: AsyncSession,
        candidates: Sequence[RouteCandidate],
    ) -> dict[UUID, RouteSegment]:
        repository = self._route_segment_repository_factory(session)
        segment_ids = tuple(
            dict.fromkeys(
                segment_id
                for candidate in candidates
                for segment_id in candidate.segment_ids
            )
        )
        segments = await repository.list_by_ids(segment_ids)
        return {segment.id: segment for segment in segments}

    def _build_route_snapshots(
        self,
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

    def _build_checkout_url(
        self,
        *,
        route_id: UUID,
        search_id: UUID,
        provider_offer_id: str | None,
    ) -> str:
        parsed_url = urlsplit(self._settings.mock_checkout_base_url)
        query_params = dict(parse_qsl(parsed_url.query, keep_blank_values=True))
        query_params["route_id"] = str(route_id)
        query_params["search_id"] = str(search_id)
        if provider_offer_id:
            query_params["provider_offer_id"] = provider_offer_id

        return urlunsplit(
            (
                parsed_url.scheme,
                parsed_url.netloc,
                parsed_url.path,
                urlencode(query_params),
                parsed_url.fragment,
            )
        )


__all__ = [
    "CheckoutLinkInfo",
    "DecimalRange",
    "EffectiveResultsQuery",
    "IntegerRange",
    "RouteListView",
    "SearchHandle",
    "SearchResultsPage",
    "SearchService",
    "SearchValidationError",
    "TransferFacet",
    "TransportTypeFacet",
]
