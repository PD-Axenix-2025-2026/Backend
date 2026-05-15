from __future__ import annotations

from datetime import datetime
from decimal import Decimal

import pytest
from app.adapters.database_route_search import DatabaseRouteSearchAdapter
from app.adapters.route_search_orchestrator import RouteSearchOrchestrator
from app.models.enums import TransportType
from app.models.location import Location
from app.models.route_segment import RouteSegment
from app.services.search.contracts import RouteCandidate, RouteSearchCriteria

from tests.support.route_search import (
    MOSCOW_TZ,
    build_candidate,
    build_carrier,
    build_db_segment,
    build_location,
    build_provider_segment,
    build_search_criteria,
)


class _StubExternalAdapter:
    def __init__(self, results: list[RouteCandidate]) -> None:
        self._results = results
        self.calls: list[RouteSearchCriteria] = []

    async def search(self, criteria: RouteSearchCriteria) -> list[RouteCandidate]:
        self.calls.append(criteria)
        return list(self._results)


class _StubDatabaseAdapter(DatabaseRouteSearchAdapter):
    def __init__(self, results: list[RouteCandidate]) -> None:
        self._results = results
        self.calls: list[RouteSearchCriteria] = []

    async def search(self, criteria: RouteSearchCriteria) -> list[RouteCandidate]:
        self.calls.append(criteria)
        return list(self._results)


@pytest.mark.asyncio
async def test_orchestrator_skips_database_in_direct_only_mode() -> None:
    criteria = _build_criteria(max_transfers=0)
    external_adapter = _StubExternalAdapter(
        results=[build_candidate(source="rzd_api", transfers=0)]
    )
    database_adapter = _StubDatabaseAdapter(
        results=[build_candidate(source="database", transfers=0)]
    )

    orchestrator = RouteSearchOrchestrator([external_adapter, database_adapter])
    results = await orchestrator.search(criteria)

    assert [candidate.source for candidate in results] == ["rzd_api"]
    assert len(external_adapter.calls) == 1
    assert database_adapter.calls == []


@pytest.mark.asyncio
async def test_orchestrator_keeps_only_db_transfer_routes_with_external_results() -> (
    None
):
    criteria = _build_criteria(max_transfers=2)
    external_adapter = _StubExternalAdapter(
        results=[build_candidate(source="rzd_api", transfers=0)]
    )
    database_adapter = _StubDatabaseAdapter(
        results=[
            build_candidate(source="database", transfers=0),
            build_candidate(source="database", transfers=1, segment_count=2),
        ]
    )

    orchestrator = RouteSearchOrchestrator([external_adapter, database_adapter])
    results = await orchestrator.search(criteria)

    assert [(candidate.source, candidate.transfers) for candidate in results] == [
        ("rzd_api", 0),
        ("database", 1),
    ]
    assert len(external_adapter.calls) == 1
    assert len(database_adapter.calls) == 1


@pytest.mark.asyncio
async def test_orchestrator_keeps_db_routes_when_external_empty() -> None:
    criteria = _build_criteria(max_transfers=2)
    external_adapter = _StubExternalAdapter(results=[])
    database_adapter = _StubDatabaseAdapter(
        results=[
            build_candidate(source="database", transfers=0),
            build_candidate(source="database", transfers=1, segment_count=2),
        ]
    )

    orchestrator = RouteSearchOrchestrator([external_adapter, database_adapter])
    results = await orchestrator.search(criteria)

    assert [(candidate.source, candidate.transfers) for candidate in results] == [
        ("database", 0),
        ("database", 1),
    ]
    assert len(external_adapter.calls) == 1
    assert len(database_adapter.calls) == 1


@pytest.mark.asyncio
async def test_orchestrator_deduplicates_matching_transfer_routes_across_sources() -> (
    None
):
    origin = build_location(code="MOW", name="Moscow")
    hub = build_location(code="KZN", name="Kazan")
    destination = build_location(code="SPB", name="Saint Petersburg")
    first_leg, second_leg = _build_matching_transfer_segments(
        origin=origin,
        hub=hub,
        destination=destination,
    )

    external_candidate = build_candidate(
        source="yandex_rasp_api",
        transfers=1,
        segment_count=2,
        total_price=Decimal("7300.00"),
        total_duration_minutes=360,
        resolved_segments=(
            build_provider_segment(
                origin=origin,
                destination=hub,
                transport_type=TransportType.plane,
                departure_at=first_leg.departure_at,
                arrival_at=first_leg.arrival_at,
                segment_code="S7 2201",
                carrier_name="S7",
                carrier_code="S7",
            ),
            build_provider_segment(
                origin=hub,
                destination=destination,
                transport_type=TransportType.train,
                departure_at=second_leg.departure_at,
                arrival_at=second_leg.arrival_at,
                segment_code="RZD 300",
                carrier_name="Russian Railways",
                carrier_code="RZD",
            ),
        ),
    )
    database_candidate = build_candidate(
        source="database",
        transfers=1,
        segment_ids=(first_leg.id, second_leg.id),
        total_price=Decimal("7300.00"),
        total_duration_minutes=360,
        resolved_segments=(first_leg, second_leg),
    )

    external_adapter = _StubExternalAdapter([external_candidate])
    database_adapter = _StubDatabaseAdapter([database_candidate])
    orchestrator = RouteSearchOrchestrator([external_adapter, database_adapter])

    results = await orchestrator.search(_build_criteria(max_transfers=3))

    assert [(candidate.source, candidate.transfers) for candidate in results] == [
        ("yandex_rasp_api", 1),
    ]
    assert len(external_adapter.calls) == 1
    assert len(database_adapter.calls) == 1


def _build_criteria(*, max_transfers: int | None) -> RouteSearchCriteria:
    origin = build_location(code="ORI", name="Origin")
    destination = build_location(code="DST", name="Destination")
    return build_search_criteria(
        origin_id=origin.id,
        origin_type=origin.location_type,
        destination_id=destination.id,
        destination_type=destination.location_type,
        max_transfers=max_transfers,
    )


def _build_matching_transfer_segments(
    *,
    origin: Location,
    hub: Location,
    destination: Location,
) -> tuple[RouteSegment, RouteSegment]:
    plane_carrier = build_carrier(
        code="S7",
        name="S7",
        transport_type=TransportType.plane,
    )
    train_carrier = build_carrier(
        code="RZD",
        name="Russian Railways",
        transport_type=TransportType.train,
    )
    first_leg = build_db_segment(
        origin=origin,
        destination=hub,
        carrier=plane_carrier,
        transport_type=TransportType.plane,
        departure_at=datetime(2026, 4, 14, 10, 0, tzinfo=MOSCOW_TZ),
        arrival_at=datetime(2026, 4, 14, 11, 40, tzinfo=MOSCOW_TZ),
        price_amount=Decimal("5200.00"),
        segment_code="S7 2201",
    )
    second_leg = build_db_segment(
        origin=hub,
        destination=destination,
        carrier=train_carrier,
        transport_type=TransportType.train,
        departure_at=datetime(2026, 4, 14, 13, 10, tzinfo=MOSCOW_TZ),
        arrival_at=datetime(2026, 4, 14, 16, 10, tzinfo=MOSCOW_TZ),
        price_amount=Decimal("2100.00"),
        segment_code="RZD 300",
    )
    return first_leg, second_leg
