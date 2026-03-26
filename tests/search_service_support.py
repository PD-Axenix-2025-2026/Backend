from __future__ import annotations

import asyncio
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal
from uuid import UUID, uuid4

from app.core.config import Settings
from app.models.carrier import Carrier
from app.models.enums import LocationType, TransportType
from app.models.location import Location
from app.models.route_segment import RouteSegment
from app.services.models import (
    CheckoutLinkInfo,
    PassengerCounts,
    RouteCandidate,
    RouteSearchCriteria,
    RouteSearchPreferences,
    RouteSnapshot,
    SearchHandle,
    SearchResultsPage,
    SearchResultsQuery,
    SearchSortOption,
    SearchStatus,
)
from app.services.ports import LocationReadPort, RouteSearchPort, RouteSegmentReadPort
from app.services.runtime import SearchRuntimeCoordinator
from app.services.search_store import InMemorySearchStore
from app.services.search_validation import SearchCriteriaValidator
from app.services.use_cases import (
    CreateCheckoutLinkUseCase,
    CreateSearchUseCase,
    GetRouteDetailUseCase,
    GetSearchResultsUseCase,
    RunSearchUseCase,
)

MOSCOW_TZ = timezone(timedelta(hours=3))


class FakeLocationReader(LocationReadPort):
    def __init__(self, locations: dict[UUID, Location]) -> None:
        self._locations = locations

    async def get_by_id(self, location_id: UUID) -> Location | None:
        return self._locations.get(location_id)

    async def list_by_prefix(
        self,
        prefix: str,
        limit: int = 10,
        location_types: tuple[LocationType, ...] = (),
    ) -> list[Location]:
        locations = list(self._locations.values())
        return locations[:limit]


class FakeRouteSegmentReader(RouteSegmentReadPort):
    def __init__(self, segments: dict[UUID, RouteSegment]) -> None:
        self._segments = segments

    async def list_by_ids(self, segment_ids: Sequence[UUID]) -> list[RouteSegment]:
        return [self._segments[segment_id] for segment_id in segment_ids]


class FakeRouteAggregationService(RouteSearchPort):
    def __init__(self, results: list[RouteCandidate]) -> None:
        self._results = results
        self.calls: list[RouteSearchCriteria] = []

    async def search(
        self,
        criteria: RouteSearchCriteria,
    ) -> list[RouteCandidate]:
        self.calls.append(criteria)
        return list(self._results)


class SearchHarness:
    def __init__(
        self,
        create_search_use_case: CreateSearchUseCase,
        get_search_results_use_case: GetSearchResultsUseCase,
        get_route_detail_use_case: GetRouteDetailUseCase,
        create_checkout_link_use_case: CreateCheckoutLinkUseCase,
        runtime_coordinator: SearchRuntimeCoordinator,
    ) -> None:
        self._create_search_use_case = create_search_use_case
        self._get_search_results_use_case = get_search_results_use_case
        self._get_route_detail_use_case = get_route_detail_use_case
        self._create_checkout_link_use_case = create_checkout_link_use_case
        self._runtime_coordinator = runtime_coordinator

    async def create_search(self, criteria: RouteSearchCriteria) -> SearchHandle:
        return await self._create_search_use_case.execute(criteria)

    async def get_results(
        self,
        search_id: UUID,
        query: SearchResultsQuery,
    ) -> SearchResultsPage:
        return await self._get_search_results_use_case.execute(search_id, query)

    async def get_route_detail(self, route_id: UUID) -> RouteSnapshot:
        return await self._get_route_detail_use_case.execute(route_id)

    async def build_checkout_link(
        self,
        route_id: UUID,
        provider_offer_id: str | None = None,
    ) -> CheckoutLinkInfo:
        return await self._create_checkout_link_use_case.execute(
            route_id,
            provider_offer_id,
        )

    async def shutdown(self) -> None:
        await self._runtime_coordinator.shutdown()


@dataclass(slots=True, frozen=True)
class SearchFixture:
    service: SearchHarness
    aggregation_service: FakeRouteAggregationService
    criteria: RouteSearchCriteria
    origin: Location
    destination: Location
    plane_segment: RouteSegment
    train_segment: RouteSegment


def build_location(
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


def build_search_service(
    *,
    locations: dict[UUID, Location],
    segments: dict[UUID, RouteSegment],
    candidates: list[RouteCandidate],
) -> tuple[SearchHarness, FakeRouteAggregationService]:
    aggregation_service = FakeRouteAggregationService(results=candidates)
    location_reader = FakeLocationReader(locations)
    route_segment_reader = FakeRouteSegmentReader(segments)
    search_store = InMemorySearchStore()
    settings = Settings(
        database_url="sqlite+aiosqlite:///./test.db",
        search_ttl_seconds=60,
        search_poll_after_ms=100,
        mock_checkout_base_url="https://example.com/checkout",
        checkout_link_ttl_seconds=120,
    )
    validator = SearchCriteriaValidator(location_reader=location_reader)
    run_search_use_case = RunSearchUseCase(
        route_search_port=aggregation_service,
        route_segment_reader=route_segment_reader,
        search_state_store=search_store,
    )
    runtime_coordinator = SearchRuntimeCoordinator(
        run_search_use_case=run_search_use_case,
        search_state_store=search_store,
    )
    service = SearchHarness(
        create_search_use_case=CreateSearchUseCase(
            settings=settings,
            validator=validator,
            search_state_store=search_store,
            runtime_coordinator=runtime_coordinator,
        ),
        get_search_results_use_case=GetSearchResultsUseCase(search_store),
        get_route_detail_use_case=GetRouteDetailUseCase(search_store),
        create_checkout_link_use_case=CreateCheckoutLinkUseCase(
            settings=settings,
            search_state_store=search_store,
        ),
        runtime_coordinator=runtime_coordinator,
    )
    return service, aggregation_service


def build_search_fixture() -> SearchFixture:
    origin = build_location(
        location_id=uuid4(),
        code="MOW",
        name="Moscow",
        location_type=LocationType.city,
    )
    destination = build_location(
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
    service, aggregation_service = build_search_service(
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

    return SearchFixture(
        service=service,
        aggregation_service=aggregation_service,
        criteria=criteria,
        origin=origin,
        destination=destination,
        plane_segment=plane_segment,
        train_segment=train_segment,
    )


async def wait_for_completion(
    service: SearchHarness,
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


async def create_completed_search(
    fixture: SearchFixture,
) -> tuple[UUID, SearchStatus]:
    handle = await fixture.service.create_search(fixture.criteria)
    completion_status = await wait_for_completion(
        fixture.service,
        handle.search_id,
    )
    return handle.search_id, completion_status


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


__all__ = [
    "SearchFixture",
    "build_location",
    "build_search_fixture",
    "build_search_service",
    "create_completed_search",
]
