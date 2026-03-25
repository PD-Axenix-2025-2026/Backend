from __future__ import annotations

import asyncio
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal
from typing import cast
from uuid import UUID, uuid4

import pytest
from app.core.config import Settings
from app.models.carrier import Carrier
from app.models.enums import LocationType, TransportType
from app.models.location import Location
from app.models.route_segment import RouteSegment
from app.services.contracts import (
    PassengerCounts,
    RouteCandidate,
    RouteSearchCriteria,
    RouteSearchPreferences,
    SearchResultsQuery,
    SearchSortOption,
    SearchStatus,
)
from app.services.route_aggregation import RouteAggregationService
from app.services.search_service import SearchService, SearchValidationError
from app.services.search_store import InMemorySearchStore, SearchNotFoundError, utc_now
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

MOSCOW_TZ = timezone(timedelta(hours=3))


class _DummySessionContext:
    async def __aenter__(self) -> AsyncSession:
        return cast(AsyncSession, object())

    async def __aexit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        tb: object,
    ) -> None:
        return None


class _DummySessionFactory:
    def __call__(self) -> _DummySessionContext:
        return _DummySessionContext()


class _FakeLocationRepository:
    def __init__(self, locations: dict[UUID, Location]) -> None:
        self._locations = locations

    async def get_by_id(self, location_id: UUID) -> Location | None:
        return self._locations.get(location_id)


class _FakeRouteSegmentRepository:
    def __init__(self, segments: dict[UUID, RouteSegment]) -> None:
        self._segments = segments

    async def list_by_ids(self, segment_ids: Sequence[UUID]) -> list[RouteSegment]:
        return [self._segments[segment_id] for segment_id in segment_ids]


class _FakeRouteAggregationService(RouteAggregationService):
    def __init__(self, results: list[RouteCandidate]) -> None:
        super().__init__(providers=[])
        self._results = results
        self.calls: list[RouteSearchCriteria] = []

    async def search(self, criteria: RouteSearchCriteria) -> list[RouteCandidate]:
        self.calls.append(criteria)
        return list(self._results)


@dataclass(slots=True, frozen=True)
class _SearchFixture:
    service: SearchService
    aggregation_service: _FakeRouteAggregationService
    criteria: RouteSearchCriteria
    origin: Location
    destination: Location
    plane_segment: RouteSegment
    train_segment: RouteSegment


def _build_location(
    *,
    location_id: UUID,
    code: str,
    name: str,
    location_type: LocationType,
    city_name: str | None = None,
) -> Location:
    return Location(
        id=location_id,
        code=code,
        name=name,
        city_name=city_name,
        country_code="RU",
        location_type=location_type,
        timezone="Europe/Moscow",
    )


def _build_carrier(
    *,
    code: str,
    name: str,
    transport_type: TransportType,
) -> Carrier:
    return Carrier(
        id=uuid4(),
        code=code,
        name=name,
        transport_type=transport_type,
    )


def _build_segment(
    *,
    segment_id: UUID,
    origin: Location,
    destination: Location,
    carrier: Carrier,
    transport_type: TransportType,
    departure_at: datetime,
    arrival_at: datetime,
    price_amount: Decimal,
    segment_code: str,
) -> RouteSegment:
    segment = RouteSegment(
        id=segment_id,
        origin_location_id=origin.id,
        destination_location_id=destination.id,
        carrier_id=carrier.id,
        transport_type=transport_type,
        segment_code=segment_code,
        departure_at=departure_at,
        arrival_at=arrival_at,
        duration_minutes=0,
        price_amount=price_amount,
        currency_code="RUB",
        available_seats=12,
        source_system="database",
        source_record_id=f"record-{segment_code}",
        valid_from=departure_at - timedelta(days=1),
        origin_location=origin,
        destination_location=destination,
        carrier=carrier,
    )
    segment.sync_duration_minutes()
    return segment


def _build_search_service(
    *,
    locations: dict[UUID, Location],
    segments: dict[UUID, RouteSegment],
    candidates: list[RouteCandidate],
) -> tuple[SearchService, _FakeRouteAggregationService]:
    aggregation_service = _FakeRouteAggregationService(results=candidates)
    settings = Settings(
        database_url="sqlite+aiosqlite:///./test.db",
        search_ttl_seconds=60,
        search_poll_after_ms=100,
        mock_checkout_base_url="https://example.com/checkout",
        checkout_link_ttl_seconds=120,
    )
    service = SearchService(
        settings=settings,
        session_factory=cast(async_sessionmaker[AsyncSession], _DummySessionFactory()),
        search_store=InMemorySearchStore(),
        location_repository_factory=lambda _session: _FakeLocationRepository(locations),
        route_segment_repository_factory=lambda _session: _FakeRouteSegmentRepository(
            segments
        ),
        route_aggregation_factory=lambda _session: aggregation_service,
    )
    return service, aggregation_service


def _build_search_fixture() -> _SearchFixture:
    origin = _build_location(
        location_id=uuid4(),
        code="MOW",
        name="Moscow",
        location_type=LocationType.city,
    )
    destination = _build_location(
        location_id=uuid4(),
        code="LED",
        name="Saint Petersburg",
        location_type=LocationType.city,
    )
    plane_segment = _build_segment(
        segment_id=uuid4(),
        origin=origin,
        destination=destination,
        carrier=_build_carrier(
            code="SU",
            name="Aeroflot",
            transport_type=TransportType.plane,
        ),
        transport_type=TransportType.plane,
        departure_at=datetime(2026, 4, 14, 7, 45, tzinfo=MOSCOW_TZ),
        arrival_at=datetime(2026, 4, 14, 9, 25, tzinfo=MOSCOW_TZ),
        price_amount=Decimal("3800.00"),
        segment_code="SU 32",
    )
    train_segment = _build_segment(
        segment_id=uuid4(),
        origin=origin,
        destination=destination,
        carrier=_build_carrier(
            code="RZD",
            name="Russian Railways",
            transport_type=TransportType.train,
        ),
        transport_type=TransportType.train,
        departure_at=datetime(2026, 4, 14, 8, 0, tzinfo=MOSCOW_TZ),
        arrival_at=datetime(2026, 4, 14, 12, 0, tzinfo=MOSCOW_TZ),
        price_amount=Decimal("4500.00"),
        segment_code="RZD 120",
    )
    service, aggregation_service = _build_search_service(
        locations={origin.id: origin, destination.id: destination},
        segments={
            plane_segment.id: plane_segment,
            train_segment.id: train_segment,
        },
        candidates=[
            RouteCandidate(
                source="database",
                segment_ids=(train_segment.id,),
                total_price=train_segment.price_amount,
                total_duration_minutes=train_segment.duration_minutes,
                transfers=0,
            ),
            RouteCandidate(
                source="database",
                segment_ids=(plane_segment.id,),
                total_price=plane_segment.price_amount,
                total_duration_minutes=plane_segment.duration_minutes,
                transfers=0,
            ),
        ],
    )
    criteria = RouteSearchCriteria(
        origin_id=origin.id,
        origin_type=origin.location_type,
        destination_id=destination.id,
        destination_type=destination.location_type,
        travel_date=date(2026, 4, 14),
        passengers=PassengerCounts(adults=1),
        preferences=RouteSearchPreferences(sort=SearchSortOption.best),
    )

    return _SearchFixture(
        service=service,
        aggregation_service=aggregation_service,
        criteria=criteria,
        origin=origin,
        destination=destination,
        plane_segment=plane_segment,
        train_segment=train_segment,
    )


async def _wait_for_completion(
    service: SearchService,
    search_id: UUID,
) -> SearchStatus:
    status = SearchStatus.pending
    for _ in range(10):
        page = await service.get_results(search_id, SearchResultsQuery())
        status = page.status
        if page.is_complete:
            return status
        await asyncio.sleep(0)
    return status


async def _create_completed_search(
    fixture: _SearchFixture,
) -> tuple[UUID, SearchStatus]:
    handle = await fixture.service.create_search(fixture.criteria)
    completion_status = await _wait_for_completion(
        fixture.service,
        handle.search_id,
    )
    return handle.search_id, completion_status


@pytest.mark.asyncio
async def test_search_service_completes_search_and_marks_best_route() -> None:
    fixture = _build_search_fixture()

    try:
        search_id, completion_status = await _create_completed_search(fixture)
        page = await fixture.service.get_results(search_id, SearchResultsQuery())
    finally:
        await fixture.service.shutdown()

    assert completion_status == SearchStatus.complete
    assert fixture.aggregation_service.calls == [fixture.criteria]
    assert page.total_found == 2
    assert page.items[0].labels == ("best", "direct")
    assert page.items[0].route.total_price.amount == Decimal("3800.00")
    assert page.items[1].labels == ("direct",)


@pytest.mark.asyncio
async def test_search_service_filters_results_and_builds_checkout_link() -> None:
    fixture = _build_search_fixture()

    try:
        search_id, _completion_status = await _create_completed_search(fixture)
        page = await fixture.service.get_results(search_id, SearchResultsQuery())
        filtered_page = await fixture.service.get_results(
            search_id,
            SearchResultsQuery(
                sort=SearchSortOption.price,
                transport_types=(TransportType.train,),
                max_price=Decimal("5000.00"),
            ),
        )
        route = await fixture.service.get_route_detail(page.items[0].route.route_id)
        checkout = await fixture.service.build_checkout_link(
            route.route_id,
            provider_offer_id="offer-1",
        )
    finally:
        await fixture.service.shutdown()

    assert filtered_page.total_found == 1
    assert filtered_page.items[0].route.transport_types == (TransportType.train,)
    assert filtered_page.items[0].labels == ("direct",)
    assert route.source == "database"
    assert "provider_offer_id=offer-1" in checkout.url


@pytest.mark.asyncio
async def test_search_service_rejects_invalid_locations() -> None:
    origin = _build_location(
        location_id=uuid4(),
        code="MOW",
        name="Moscow",
        location_type=LocationType.city,
    )
    service, _aggregation_service = _build_search_service(
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
