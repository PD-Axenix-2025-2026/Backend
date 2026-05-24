import asyncio
import logging
from collections.abc import AsyncIterator
from datetime import date, timedelta
from decimal import Decimal
from uuid import UUID, uuid4

import pytest
import pytest_asyncio
from app.core.config import Settings
from app.models.enums import LocationType, TransportType
from app.services.application.use_cases import CreateSearchUseCase, RunSearchUseCase
from app.services.search.contracts import (
    RouteCandidate,
    RouteCandidateBatch,
    RouteSearchCriteria,
    SearchResultsQuery,
    SearchSortOption,
    SearchStatus,
)
from app.services.search.snapshot_builder import build_route_snapshot
from app.services.search.store.memory import InMemorySearchStore
from app.services.search.store.models import RouteSnapshot, SearchNotFoundError, utc_now
from app.services.search.validation import (
    SearchCriteriaValidator,
    SearchValidationError,
)

from tests.support.search_service import (
    FakeLocationReader,
    FakeRouteAggregationService,
    FakeRouteSegmentReader,
    SearchFixture,
    build_location,
    build_search_fixture,
    build_search_service,
    create_completed_search,
)


class _FakeRuntimeCoordinator:
    def __init__(self) -> None:
        self.dispatch_calls: list[RouteSearchCriteria] = []

    def dispatch(
        self,
        *,
        search_id: UUID,
        criteria: RouteSearchCriteria,
    ) -> None:
        self.dispatch_calls.append(criteria)


class _FakeSearchResultsCache:
    def __init__(self, routes: list[RouteSnapshot] | None = None) -> None:
        self._routes = routes
        self.get_calls: list[RouteSearchCriteria] = []
        self.set_calls: list[tuple[RouteSearchCriteria, list[RouteSnapshot]]] = []

    async def get(self, criteria: RouteSearchCriteria) -> list[RouteSnapshot] | None:
        self.get_calls.append(criteria)
        return self._routes

    async def set(
        self,
        criteria: RouteSearchCriteria,
        routes: list[RouteSnapshot],
    ) -> None:
        self.set_calls.append((criteria, routes))


class _ControlledBatchRouteSearch:
    def __init__(
        self,
        first_batch: list[RouteCandidate],
        final_batch: list[RouteCandidate],
    ) -> None:
        self.first_batch = first_batch
        self.final_batch = final_batch
        self.partial_published = asyncio.Event()
        self.release_final = asyncio.Event()

    async def search(
        self,
        criteria: RouteSearchCriteria,
    ) -> list[RouteCandidate]:
        return self.final_batch

    async def search_batches(
        self,
        criteria: RouteSearchCriteria,
    ) -> AsyncIterator[RouteCandidateBatch]:
        yield RouteCandidateBatch(candidates=tuple(self.first_batch))
        self.partial_published.set()
        await self.release_final.wait()
        yield RouteCandidateBatch(
            candidates=tuple(self.final_batch),
            is_final=True,
        )


@pytest_asyncio.fixture
async def search_fixture() -> AsyncIterator[SearchFixture]:
    fixture = build_search_fixture()
    try:
        yield fixture
    finally:
        await fixture.service.shutdown()


@pytest.mark.asyncio
async def test_search_service_completes_search_and_marks_best_route(
    search_fixture: SearchFixture,
) -> None:
    search_id, completion_status = await create_completed_search(search_fixture)
    page = await search_fixture.service.get_results(search_id, SearchResultsQuery())

    total_price = page.items[0].route.total_price

    assert completion_status == SearchStatus.complete
    assert search_fixture.aggregation_service.calls == [search_fixture.criteria]
    assert page.total_found == 2
    assert page.items[0].labels == ("best", "direct")
    assert total_price is not None
    assert total_price.amount == Decimal("3800.00")
    assert page.items[1].labels == ("direct",)


@pytest.mark.asyncio
async def test_search_service_filters_results_and_builds_checkout_link(
    search_fixture: SearchFixture,
) -> None:
    search_id, _completion_status = await create_completed_search(search_fixture)
    page = await search_fixture.service.get_results(search_id, SearchResultsQuery())
    filtered_page = await search_fixture.service.get_results(
        search_id,
        SearchResultsQuery(
            sort=SearchSortOption.price,
            transport_types=(TransportType.train,),
            max_price=Decimal("5000.00"),
        ),
    )
    route = await search_fixture.service.get_route_detail(page.items[0].route.route_id)
    checkout = await search_fixture.service.build_checkout_link(
        route.route_id,
        provider_offer_id="offer-1",
    )

    assert filtered_page.total_found == 1
    assert filtered_page.items[0].route.transport_types == (TransportType.train,)
    assert filtered_page.items[0].labels == ("direct",)
    assert route.source == "database"
    assert "provider_offer_id=offer-1" in checkout.url


@pytest.mark.asyncio
async def test_search_service_logs_search_lifecycle(
    caplog: pytest.LogCaptureFixture,
    search_fixture: SearchFixture,
) -> None:
    caplog.set_level(logging.INFO, logger="app.services.application")
    caplog.set_level(logging.INFO, logger="app.services.search.store")
    search_id, completion_status = await create_completed_search(search_fixture)

    search_service_records = [
        record for record in caplog.records if record.name == "app.services.application"
    ]
    search_store_records = [
        record
        for record in caplog.records
        if record.name == "app.services.search.store"
    ]

    assert completion_status == SearchStatus.complete
    assert any(
        "Search created" in record.getMessage()
        and getattr(record, "search_id", None) == str(search_id)
        for record in search_service_records
    )
    assert any(
        "Background search completed" in record.getMessage()
        and getattr(record, "search_id", None) == str(search_id)
        for record in search_service_records
    )
    assert any(
        "Search record marked complete" in record.getMessage()
        and getattr(record, "search_id", None) == str(search_id)
        for record in search_store_records
    )


@pytest.mark.asyncio
async def test_search_service_rejects_invalid_locations() -> None:
    origin = build_location(
        location_id=uuid4(),
        code="MOW",
        name="Moscow",
        location_type=LocationType.city,
    )
    service, _aggregation_service = build_search_service(
        locations={origin.id: origin},
        segments={},
        candidates=[],
    )
    criteria = RouteSearchCriteria(
        origin_id=origin.id,
        origin_type=origin.location_type,
        destination_id=uuid4(),
        destination_type=LocationType.city,
        travel_date=date(2026, 4, 14),
    )

    try:
        with pytest.raises(SearchValidationError):
            await service.create_search(criteria)
    finally:
        await service.shutdown()


@pytest.mark.asyncio
async def test_search_store_drops_expired_searches() -> None:
    store = InMemorySearchStore()
    criteria = RouteSearchCriteria(
        origin_id=uuid4(),
        origin_type=LocationType.city,
        destination_id=uuid4(),
        destination_type=LocationType.city,
        travel_date=date(2026, 4, 14),
    )
    search_id = uuid4()
    await store.create_search(
        search_id=search_id,
        criteria=criteria,
        expires_at=utc_now() - timedelta(seconds=1),
    )

    with pytest.raises(SearchNotFoundError):
        await store.get_search(search_id)


@pytest.mark.asyncio
async def test_search_store_appends_partial_routes(
    search_fixture: SearchFixture,
) -> None:
    store = InMemorySearchStore()
    search_id = uuid4()
    await store.create_search(
        search_id=search_id,
        criteria=search_fixture.criteria,
        expires_at=utc_now() + timedelta(seconds=60),
    )
    route = build_route_snapshot(
        search_id=search_id,
        candidate=RouteCandidate(
            source="database",
            segment_ids=(search_fixture.plane_segment.id,),
            total_price=search_fixture.plane_segment.price_amount,
            total_duration_minutes=search_fixture.plane_segment.duration_minutes,
            transfers=0,
        ),
        segments=(search_fixture.plane_segment,),
    )

    record = await store.append_routes(search_id=search_id, routes=[route])
    indexed_record, indexed_route = await store.get_route(route.route_id)

    assert record.status == SearchStatus.partial
    assert record.last_update == 1
    assert indexed_record.search_id == search_id
    assert indexed_route == route


@pytest.mark.asyncio
async def test_run_search_use_case_publishes_partial_batch(
    search_fixture: SearchFixture,
) -> None:
    search_store = InMemorySearchStore()
    search_id = uuid4()
    await search_store.create_search(
        search_id=search_id,
        criteria=search_fixture.criteria,
        expires_at=utc_now() + timedelta(seconds=60),
    )
    first_candidate = RouteCandidate(
        source="database",
        segment_ids=(search_fixture.plane_segment.id,),
        total_price=search_fixture.plane_segment.price_amount,
        total_duration_minutes=search_fixture.plane_segment.duration_minutes,
        transfers=0,
    )
    final_candidate = RouteCandidate(
        source="database",
        segment_ids=(search_fixture.train_segment.id,),
        total_price=search_fixture.train_segment.price_amount,
        total_duration_minutes=search_fixture.train_segment.duration_minutes,
        transfers=0,
    )
    route_search = _ControlledBatchRouteSearch(
        first_batch=[first_candidate],
        final_batch=[first_candidate, final_candidate],
    )
    run_search_use_case = RunSearchUseCase(
        route_search_port=route_search,
        route_segment_reader=FakeRouteSegmentReader(
            {
                search_fixture.plane_segment.id: search_fixture.plane_segment,
                search_fixture.train_segment.id: search_fixture.train_segment,
            }
        ),
        search_state_store=search_store,
    )
    task = asyncio.create_task(
        run_search_use_case.execute(
            search_id=search_id,
            criteria=search_fixture.criteria,
        )
    )

    await route_search.partial_published.wait()
    partial_record = await search_store.get_search(search_id)
    partial_status = partial_record.status
    partial_route_count = len(partial_record.routes)
    partial_route_id = partial_record.routes[0].route_id
    route_search.release_final.set()
    final_routes = await task
    complete_record = await search_store.get_search(search_id)

    assert partial_status == SearchStatus.partial
    assert partial_route_count == 1
    assert complete_record.status == SearchStatus.complete
    assert complete_record.routes[0].route_id == partial_route_id
    assert len(final_routes) == 2


@pytest.mark.asyncio
async def test_create_search_use_case_returns_completed_search_from_cache(
    search_fixture: SearchFixture,
) -> None:
    search_store = InMemorySearchStore()
    cached_route = build_route_snapshot(
        search_id=uuid4(),
        candidate=RouteCandidate(
            source="database",
            segment_ids=(search_fixture.plane_segment.id,),
            total_price=search_fixture.plane_segment.price_amount,
            total_duration_minutes=search_fixture.plane_segment.duration_minutes,
            transfers=0,
        ),
        segments=(search_fixture.plane_segment,),
    )
    cache = _FakeSearchResultsCache(routes=[cached_route])
    runtime_coordinator = _FakeRuntimeCoordinator()
    use_case = CreateSearchUseCase(
        settings=Settings(search_ttl_seconds=60, search_poll_after_ms=100),
        validator=SearchCriteriaValidator(
            location_reader=FakeLocationReader(
                {
                    search_fixture.origin.id: search_fixture.origin,
                    search_fixture.destination.id: search_fixture.destination,
                }
            )
        ),
        search_state_store=search_store,
        runtime_coordinator=runtime_coordinator,
        results_cache=cache,
    )

    handle = await use_case.execute(search_fixture.criteria)
    record = await search_store.get_search(handle.search_id)

    assert handle.status == SearchStatus.complete
    assert record.status == SearchStatus.complete
    assert record.routes[0].search_id == handle.search_id
    assert record.routes[0].route_id != cached_route.route_id
    assert runtime_coordinator.dispatch_calls == []


@pytest.mark.asyncio
async def test_run_search_use_case_stores_completed_routes_in_cache(
    search_fixture: SearchFixture,
) -> None:
    search_store = InMemorySearchStore()
    search_id = uuid4()
    await search_store.create_search(
        search_id=search_id,
        criteria=search_fixture.criteria,
        expires_at=utc_now() + timedelta(seconds=60),
    )
    candidate = RouteCandidate(
        source="database",
        segment_ids=(search_fixture.plane_segment.id,),
        total_price=search_fixture.plane_segment.price_amount,
        total_duration_minutes=search_fixture.plane_segment.duration_minutes,
        transfers=0,
    )
    cache = _FakeSearchResultsCache()
    aggregation_service = FakeRouteAggregationService(results=[candidate])
    run_search_use_case = RunSearchUseCase(
        route_search_port=aggregation_service,
        route_segment_reader=FakeRouteSegmentReader(
            {search_fixture.plane_segment.id: search_fixture.plane_segment}
        ),
        search_state_store=search_store,
        results_cache=cache,
    )

    routes = await run_search_use_case.execute(
        search_id=search_id,
        criteria=search_fixture.criteria,
    )

    assert len(routes) == 1
    assert cache.set_calls == [(search_fixture.criteria, routes)]
