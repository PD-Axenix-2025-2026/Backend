from __future__ import annotations

from collections.abc import Sequence
from datetime import date, datetime
from decimal import Decimal
from unittest.mock import AsyncMock

import pytest
from app.adapters import database_route_search as database_route_search_module
from app.adapters.database_route_search import (
    DatabaseRouteSearchAdapter,
    EndpointScope,
    _load_endpoint_scope,
)
from app.models.enums import TransportType
from app.models.location import Location
from app.models.route_segment import RouteSegment
from app.repositories.location_repository import LocationRepository
from app.services.search.contracts import RouteCandidate, RouteSearchCriteria
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from tests.support.route_search import (
    MOSCOW_TZ,
    TransferLocationSet,
    build_carrier,
    build_db_segment,
    build_search_criteria,
    build_transfer_location_set,
)


@pytest.mark.asyncio
async def test_load_endpoint_scope_expands_city_children(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    locations = build_transfer_location_set()
    repository = _StubLocationRepository(
        locations_by_id={
            locations.origin_city.id: locations.origin_city,
            locations.destination_city.id: locations.destination_city,
        }
    )
    criteria = _build_criteria(locations=locations, max_transfers=1)

    async def fake_load_child_locations(
        *,
        repository: _StubLocationRepository,
        locations: Sequence[Location],
    ) -> list[Location]:
        assert len(locations) == 2
        return [locations_set.origin_airport, locations_set.destination_airport]

    locations_set = locations
    monkeypatch.setattr(
        database_route_search_module,
        "_load_child_locations",
        fake_load_child_locations,
    )

    endpoint_scope = await _load_endpoint_scope(
        repository=repository,
        criteria=criteria,
    )

    assert endpoint_scope == EndpointScope(
        origin_endpoint_ids=frozenset(
            {locations.origin_city.id, locations.origin_airport.id}
        ),
        destination_endpoint_ids=frozenset(
            {locations.destination_city.id, locations.destination_airport.id}
        ),
    )


@pytest.mark.asyncio
async def test_database_route_search_supports_city_expansion_and_transfers(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    locations = build_transfer_location_set()
    direct_segment, first_leg, second_leg = _build_route_segments(locations)
    endpoint_scope = EndpointScope(
        origin_endpoint_ids=frozenset(
            {locations.origin_city.id, locations.origin_airport.id}
        ),
        destination_endpoint_ids=frozenset(
            {locations.destination_city.id, locations.destination_airport.id}
        ),
    )

    async def fake_load_endpoint_scope(**_: object) -> EndpointScope:
        return endpoint_scope

    async def fake_list_active_for_planning(
        _repository_self: object,
        criteria: RouteSearchCriteria,
        *,
        planning_window_days: int,
    ) -> list[RouteSegment]:
        assert planning_window_days == (
            1 if criteria.preferences.max_transfers == 0 else 3
        )
        return [direct_segment, first_leg, second_leg]

    monkeypatch.setattr(
        database_route_search_module,
        "_load_endpoint_scope",
        fake_load_endpoint_scope,
    )
    monkeypatch.setattr(
        database_route_search_module.RouteSegmentRepository,
        "list_active_for_planning",
        fake_list_active_for_planning,
    )

    adapter = DatabaseRouteSearchAdapter(_build_session_factory())
    direct_results = await adapter.search(
        _build_criteria(locations=locations, max_transfers=0)
    )
    transfer_results = await adapter.search(
        _build_criteria(locations=locations, max_transfers=1)
    )

    assert _candidate_signature(direct_results) == [((direct_segment.id,), 0)]
    assert _candidate_signature(transfer_results) == [
        ((direct_segment.id,), 0),
        ((first_leg.id, second_leg.id), 1),
    ]


def _build_criteria(
    *,
    locations: TransferLocationSet,
    max_transfers: int | None,
) -> RouteSearchCriteria:
    return build_search_criteria(
        origin_id=locations.origin_city.id,
        origin_type=locations.origin_city.location_type,
        destination_id=locations.destination_city.id,
        destination_type=locations.destination_city.location_type,
        max_transfers=max_transfers,
        travel_date=date(2026, 6, 14),
    )


def _build_route_segments(
    locations: TransferLocationSet,
) -> tuple[RouteSegment, RouteSegment, RouteSegment]:
    plane_carrier = build_carrier(
        code="SU",
        name="Aeroflot",
        transport_type=TransportType.plane,
    )
    train_carrier = build_carrier(
        code="RZD",
        name="Russian Railways",
        transport_type=TransportType.train,
    )
    direct_segment = build_db_segment(
        origin=locations.origin_airport,
        destination=locations.destination_airport,
        carrier=plane_carrier,
        transport_type=TransportType.plane,
        departure_at=datetime(2026, 6, 14, 8, 0, tzinfo=MOSCOW_TZ),
        arrival_at=datetime(2026, 6, 14, 9, 30, tzinfo=MOSCOW_TZ),
        price_amount=Decimal("4500.00"),
        segment_code="SU 100",
        valid_from=datetime(2026, 1, 1, tzinfo=MOSCOW_TZ),
    )
    first_leg = build_db_segment(
        origin=locations.origin_airport,
        destination=locations.hub_station,
        carrier=plane_carrier,
        transport_type=TransportType.plane,
        departure_at=datetime(2026, 6, 14, 7, 0, tzinfo=MOSCOW_TZ),
        arrival_at=datetime(2026, 6, 14, 8, 30, tzinfo=MOSCOW_TZ),
        price_amount=Decimal("2000.00"),
        segment_code="SU 200",
        valid_from=datetime(2026, 1, 1, tzinfo=MOSCOW_TZ),
    )
    second_leg = build_db_segment(
        origin=locations.hub_airport,
        destination=locations.destination_airport,
        carrier=train_carrier,
        transport_type=TransportType.train,
        departure_at=datetime(2026, 6, 14, 10, 0, tzinfo=MOSCOW_TZ),
        arrival_at=datetime(2026, 6, 14, 12, 0, tzinfo=MOSCOW_TZ),
        price_amount=Decimal("1500.00"),
        segment_code="RZD 300",
        valid_from=datetime(2026, 1, 1, tzinfo=MOSCOW_TZ),
    )
    return direct_segment, first_leg, second_leg


def _candidate_signature(
    candidates: Sequence[RouteCandidate],
) -> list[tuple[tuple[object, ...], int]]:
    return [
        (tuple(candidate.segment_ids), candidate.transfers) for candidate in candidates
    ]


def _build_session_factory() -> async_sessionmaker[AsyncSession]:
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    return async_sessionmaker(
        bind=engine,
        class_=AsyncSession,
        expire_on_commit=False,
    )


class _StubLocationRepository(LocationRepository):
    def __init__(self, *, locations_by_id: dict[object, Location]) -> None:
        self.locations_by_id = locations_by_id
        self.session: AsyncSession = AsyncMock(spec=AsyncSession)

    async def get_by_id(self, location_id: object) -> Location | None:
        return self.locations_by_id.get(location_id)
