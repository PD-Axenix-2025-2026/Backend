from __future__ import annotations

from collections.abc import Generator
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta, timezone
from decimal import Decimal
from uuid import UUID, uuid4

import pytest
from app.api.dependencies import (
    get_create_checkout_link_use_case,
    get_create_search_use_case,
    get_list_locations_use_case,
    get_route_detail_use_case,
    get_search_results_use_case,
)
from app.models.enums import LocationType, TransportType
from app.models.location import Location
from app.services.models import (
    CheckoutLinkInfo,
    DecimalRange,
    IntegerRange,
    MoneySnapshot,
    PassengerCounts,
    RouteListView,
    RouteSearchCriteria,
    RouteSearchPreferences,
    RouteSegmentSnapshot,
    RouteSnapshot,
    SearchHandle,
    SearchResultsPage,
    SearchResultsQuery,
    SearchSortOption,
    SearchStatus,
    TransferFacet,
    TransportTypeFacet,
)
from app.services.use_cases import (
    CreateCheckoutLinkUseCase,
    CreateSearchUseCase,
    GetRouteDetailUseCase,
    GetSearchResultsUseCase,
    ListLocationsUseCase,
)
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient

MOSCOW_TZ = timezone(timedelta(hours=3))


class _FakeListLocationsUseCase:
    def __init__(self, locations: list[Location]) -> None:
        self._locations = locations
        self.calls: list[tuple[str, int, tuple[LocationType, ...]]] = []

    async def execute(
        self,
        prefix: str,
        limit: int = 10,
        location_types: tuple[LocationType, ...] = (),
    ) -> list[Location]:
        self.calls.append((prefix, limit, location_types))
        return self._locations[:limit]


class _FakeCreateSearchUseCase:
    def __init__(self, route: RouteSnapshot) -> None:
        self._route = route
        self.create_calls: list[RouteSearchCriteria] = []

    async def execute(self, criteria: RouteSearchCriteria) -> SearchHandle:
        self.create_calls.append(criteria)
        return SearchHandle(
            search_id=self._route.search_id,
            status=SearchStatus.pending,
            results_url=f"/api/searches/{self._route.search_id}/results",
            poll_after_ms=1000,
            expires_at=datetime(2026, 3, 25, 12, 0, tzinfo=UTC),
        )


class _FakeGetSearchResultsUseCase:
    def __init__(self, route: RouteSnapshot) -> None:
        self._route = route
        self.result_calls: list[tuple[UUID, SearchResultsQuery]] = []

    async def execute(
        self,
        search_id: UUID,
        query: SearchResultsQuery,
    ) -> SearchResultsPage:
        self.result_calls.append((search_id, query))
        return SearchResultsPage(
            search_id=search_id,
            status=SearchStatus.complete,
            is_complete=True,
            last_update=1,
            total_found=1,
            currency="RUB",
            stale_after_sec=120,
            transport_type_facets=(
                TransportTypeFacet(value=TransportType.plane, count=1),
            ),
            transfer_facets=(TransferFacet(value=0, count=1),),
            price_range=DecimalRange(
                min=self._route.total_price.amount,
                max=self._route.total_price.amount,
            ),
            duration_range=IntegerRange(
                min=self._route.duration_minutes,
                max=self._route.duration_minutes,
            ),
            items=(RouteListView(route=self._route, labels=("best", "direct")),),
        )


class _FakeGetRouteDetailUseCase:
    def __init__(self, route: RouteSnapshot) -> None:
        self._route = route

    async def execute(self, route_id: UUID) -> RouteSnapshot:
        assert route_id == self._route.route_id
        return self._route


class _FakeCreateCheckoutLinkUseCase:
    def __init__(self) -> None:
        self.checkout_calls: list[tuple[UUID, str | None]] = []

    async def execute(
        self,
        route_id: UUID,
        provider_offer_id: str | None = None,
    ) -> CheckoutLinkInfo:
        self.checkout_calls.append((route_id, provider_offer_id))
        return CheckoutLinkInfo(
            method="GET",
            url="https://example.com/checkout?route_id=test",
            expires_at=datetime(2026, 3, 25, 12, 3, tzinfo=UTC),
        )


@dataclass(slots=True, frozen=True)
class _SearchApiFixture:
    route: RouteSnapshot
    list_locations_use_case: _FakeListLocationsUseCase
    create_search_use_case: _FakeCreateSearchUseCase
    get_search_results_use_case: _FakeGetSearchResultsUseCase
    get_route_detail_use_case: _FakeGetRouteDetailUseCase
    create_checkout_link_use_case: _FakeCreateCheckoutLinkUseCase


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


def _build_route() -> RouteSnapshot:
    origin_id = uuid4()
    destination_id = uuid4()
    search_id = uuid4()
    route_id = uuid4()
    segment_id = uuid4()
    departure_at = datetime(2026, 4, 14, 7, 45, tzinfo=MOSCOW_TZ)
    arrival_at = datetime(2026, 4, 14, 9, 25, tzinfo=MOSCOW_TZ)
    segment = RouteSegmentSnapshot(
        segment_id=segment_id,
        transport_type=TransportType.plane,
        carrier="Aeroflot",
        carrier_code="SU",
        segment_code="SU 32",
        origin_id=origin_id,
        origin_code="MOW",
        origin_label="Moscow",
        destination_id=destination_id,
        destination_code="LED",
        destination_label="Saint Petersburg",
        departure_at=departure_at,
        arrival_at=arrival_at,
        duration_minutes=100,
        price=MoneySnapshot(amount=Decimal("3800.00"), currency="RUB"),
        available_seats=7,
        source_system="database",
        source_record_id="segment-1",
        valid_from=departure_at - timedelta(days=1),
        valid_to=None,
    )
    return RouteSnapshot(
        route_id=route_id,
        search_id=search_id,
        source="database",
        segment_ids=(segment_id,),
        departure_at=departure_at,
        arrival_at=arrival_at,
        duration_minutes=100,
        transfers=0,
        total_price=MoneySnapshot(amount=Decimal("3800.00"), currency="RUB"),
        transport_types=(TransportType.plane,),
        segments=(segment,),
    )


def _build_search_api_fixture() -> _SearchApiFixture:
    route = _build_route()
    list_locations_use_case = _FakeListLocationsUseCase(
        locations=[
            _build_location(
                location_id=uuid4(),
                code="MOW",
                name="Moscow",
                location_type=LocationType.city,
            ),
            _build_location(
                location_id=uuid4(),
                code="SVO",
                name="Sheremetyevo",
                location_type=LocationType.airport,
                city_name="Moscow",
            ),
        ]
    )
    create_search_use_case = _FakeCreateSearchUseCase(route=route)
    get_search_results_use_case = _FakeGetSearchResultsUseCase(route=route)
    get_route_detail_use_case = _FakeGetRouteDetailUseCase(route=route)
    create_checkout_link_use_case = _FakeCreateCheckoutLinkUseCase()
    return _SearchApiFixture(
        route=route,
        list_locations_use_case=list_locations_use_case,
        create_search_use_case=create_search_use_case,
        get_search_results_use_case=get_search_results_use_case,
        get_route_detail_use_case=get_route_detail_use_case,
        create_checkout_link_use_case=create_checkout_link_use_case,
    )


@pytest.fixture
def search_api_fixture(app: FastAPI) -> Generator[_SearchApiFixture, None, None]:
    fixture = _build_search_api_fixture()

    async def _get_fake_list_locations_use_case() -> ListLocationsUseCase:
        return fixture.list_locations_use_case  # type: ignore[return-value]

    async def _get_fake_create_search_use_case() -> CreateSearchUseCase:
        return fixture.create_search_use_case  # type: ignore[return-value]

    async def _get_fake_search_results_use_case() -> GetSearchResultsUseCase:
        return fixture.get_search_results_use_case  # type: ignore[return-value]

    async def _get_fake_route_detail_use_case() -> GetRouteDetailUseCase:
        return fixture.get_route_detail_use_case  # type: ignore[return-value]

    async def _get_fake_checkout_link_use_case() -> CreateCheckoutLinkUseCase:
        return fixture.create_checkout_link_use_case  # type: ignore[return-value]

    app.dependency_overrides[get_list_locations_use_case] = (
        _get_fake_list_locations_use_case
    )
    app.dependency_overrides[get_create_search_use_case] = (
        _get_fake_create_search_use_case
    )
    app.dependency_overrides[get_search_results_use_case] = (
        _get_fake_search_results_use_case
    )
    app.dependency_overrides[get_route_detail_use_case] = (
        _get_fake_route_detail_use_case
    )
    app.dependency_overrides[get_create_checkout_link_use_case] = (
        _get_fake_checkout_link_use_case
    )

    try:
        yield fixture
    finally:
        app.dependency_overrides.clear()


@pytest.mark.asyncio
async def test_locations_endpoint_serializes_autocomplete_items(
    app: FastAPI,
    search_api_fixture: _SearchApiFixture,
) -> None:
    async with AsyncClient(
        transport=ASGITransport(app=app),
        base_url="http://testserver",
    ) as client:
        response = await client.get(
            "/api/locations?prefix=Mo&types=city,airport&limit=5"
        )

    assert response.status_code == 200
    assert response.json()["items"][0]["label"] == "Moscow"
    assert search_api_fixture.list_locations_use_case.calls == [
        ("Mo", 5, (LocationType.city, LocationType.airport))
    ]


@pytest.mark.asyncio
async def test_search_flow_endpoints_serialize_contract(
    app: FastAPI,
    search_api_fixture: _SearchApiFixture,
) -> None:
    route = search_api_fixture.route

    async with AsyncClient(
        transport=ASGITransport(app=app),
        base_url="http://testserver",
    ) as client:
        create_response = await client.post(
            "/api/searches",
            json={
                "origin": {
                    "id": str(route.segments[0].origin_id),
                    "type": "city",
                },
                "destination": {
                    "id": str(route.segments[0].destination_id),
                    "type": "city",
                },
                "date": "2026-04-14",
                "passengers": {"adults": 2, "children": 0, "infants": 0},
                "transport_types": ["plane", "train"],
                "preferences": {"sort": "best", "max_transfers": 1},
            },
        )
        results_response = await client.get(
            f"/api/searches/{route.search_id}/results"
            "?sort=price&transport_types=plane&limit=5&offset=0"
        )
        detail_response = await client.get(f"/api/routes/{route.route_id}")
        checkout_response = await client.post(
            f"/api/routes/{route.route_id}/checkout-link",
            json={"provider_offer_id": "offer-1"},
        )

    assert create_response.status_code == 201
    assert create_response.json()["search_id"] == str(route.search_id)
    assert search_api_fixture.create_search_use_case.create_calls[0] == (
        RouteSearchCriteria(
            origin_id=route.segments[0].origin_id,
            origin_type=LocationType.city,
            destination_id=route.segments[0].destination_id,
            destination_type=LocationType.city,
            travel_date=datetime(2026, 4, 14).date(),
            passengers=PassengerCounts(adults=2, children=0, infants=0),
            transport_types=(TransportType.plane, TransportType.train),
            preferences=RouteSearchPreferences(
                sort=SearchSortOption.best,
                max_transfers=1,
            ),
        )
    )

    assert results_response.status_code == 200
    assert results_response.json()["items"][0]["route_id"] == str(route.route_id)
    assert (
        search_api_fixture.get_search_results_use_case.result_calls[0][0]
        == route.search_id
    )
    assert search_api_fixture.get_search_results_use_case.result_calls[0][
        1
    ] == SearchResultsQuery(
        last_update=0,
        sort=SearchSortOption.price,
        transport_types=(TransportType.plane,),
        limit=5,
        offset=0,
    )

    assert detail_response.status_code == 200
    assert detail_response.json()["source"] == "database"

    assert checkout_response.status_code == 200
    assert checkout_response.json()["method"] == "GET"
    assert search_api_fixture.create_checkout_link_use_case.checkout_calls == [
        (route.route_id, "offer-1")
    ]
