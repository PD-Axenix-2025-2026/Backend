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
async def test_orchestrator_deduplicates_direct_routes_with_carrier_aliases() -> None:
    origin = build_location(code="ORI", name="Origin")
    destination = build_location(code="DST", name="Destination")
    departure_at = datetime(2026, 4, 14, 20, 48, tzinfo=MOSCOW_TZ)
    arrival_at = datetime(2026, 4, 14, 22, 6, tzinfo=MOSCOW_TZ)
    yandex_segment = build_provider_segment(
        origin=origin,
        destination=destination,
        transport_type=TransportType.train,
        departure_at=departure_at,
        arrival_at=arrival_at,
        segment_code="6084",
        carrier_name="Severo-Kavkazskaya PPK",
        carrier_code="1369",
        price_amount=Decimal("135.00"),
        source_system="yandex_rasp_api",
    )
    rzd_segment = build_provider_segment(
        origin=origin,
        destination=destination,
        transport_type=TransportType.train,
        departure_at=departure_at,
        arrival_at=arrival_at,
        segment_code="6084",
        carrier_name="SKPPK",
        carrier_code=None,
        price_amount=None,
        source_system="rzd_api",
    )
    rzd_candidate = build_candidate(
        source="rzd_api",
        transfers=0,
        segment_ids=(rzd_segment.segment_id,),
        total_price=None,
        total_duration_minutes=78,
        resolved_segments=(rzd_segment,),
    )
    yandex_candidate = build_candidate(
        source="yandex_rasp_api",
        transfers=0,
        segment_ids=(yandex_segment.segment_id,),
        total_price=Decimal("135.00"),
        total_duration_minutes=78,
        resolved_segments=(yandex_segment,),
    )

    orchestrator = RouteSearchOrchestrator(
        [
            _StubExternalAdapter([rzd_candidate]),
            _StubExternalAdapter([yandex_candidate]),
        ]
    )
    results = await orchestrator.search(_build_criteria(max_transfers=0))

    assert [(candidate.source, candidate.total_price) for candidate in results] == [
        ("yandex_rasp_api", Decimal("135.00")),
    ]


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
                price_amount=Decimal("5200.00"),
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
                price_amount=Decimal("2100.00"),
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


@pytest.mark.asyncio
async def test_orchestrator_deduplicates_transfer_routes_with_carrier_aliases() -> None:
    origin = build_location(code="ORI", name="Origin")
    hub = build_location(code="HUB", name="Hub")
    destination = build_location(code="DST", name="Destination")
    first_departure = datetime(2026, 4, 14, 15, 6, tzinfo=MOSCOW_TZ)
    first_arrival = datetime(2026, 4, 14, 15, 38, tzinfo=MOSCOW_TZ)
    second_departure = datetime(2026, 4, 14, 17, 25, tzinfo=MOSCOW_TZ)
    second_arrival = datetime(2026, 4, 15, 3, 50, tzinfo=MOSCOW_TZ)
    yandex_segments = (
        build_provider_segment(
            origin=origin,
            destination=hub,
            transport_type=TransportType.train,
            departure_at=first_departure,
            arrival_at=first_arrival,
            segment_code="6525",
            carrier_name="Severo-Kavkazskaya PPK",
            carrier_code="1369",
            source_system="yandex_rasp_api",
        ),
        build_provider_segment(
            origin=hub,
            destination=destination,
            transport_type=TransportType.train,
            departure_at=second_departure,
            arrival_at=second_arrival,
            segment_code="007\u0410",
            carrier_name="Grand Service Express",
            carrier_code="63438",
            price_amount=Decimal("4200.00"),
            source_system="yandex_rasp_api",
        ),
    )
    rzd_segments = (
        build_provider_segment(
            origin=origin,
            destination=hub,
            transport_type=TransportType.train,
            departure_at=first_departure,
            arrival_at=first_arrival,
            segment_code="6525",
            carrier_name="SKPPK",
            carrier_code=None,
            source_system="rzd_api",
        ),
        build_provider_segment(
            origin=hub,
            destination=destination,
            transport_type=TransportType.train,
            departure_at=second_departure,
            arrival_at=second_arrival,
            segment_code="007A",
            carrier_name="Tavria",
            carrier_code=None,
            source_system="rzd_api",
        ),
    )
    yandex_candidate = build_candidate(
        source="yandex_rasp_api",
        transfers=1,
        segment_ids=tuple(segment.segment_id for segment in yandex_segments),
        total_price=Decimal("4200.00"),
        total_duration_minutes=764,
        resolved_segments=yandex_segments,
    )
    rzd_candidate = build_candidate(
        source="rzd_api",
        transfers=1,
        segment_ids=tuple(segment.segment_id for segment in rzd_segments),
        total_price=None,
        total_duration_minutes=764,
        resolved_segments=rzd_segments,
    )

    orchestrator = RouteSearchOrchestrator(
        [
            _StubExternalAdapter([rzd_candidate]),
            _StubExternalAdapter([yandex_candidate]),
        ]
    )
    results = await orchestrator.search(_build_criteria(max_transfers=2))

    assert [(candidate.source, candidate.total_price) for candidate in results] == [
        ("yandex_rasp_api", Decimal("4200.00")),
    ]


@pytest.mark.asyncio
async def test_orchestrator_keeps_transfer_routes_with_different_tail_segments() -> (
    None
):
    origin = build_location(code="ORI", name="Origin")
    hub = build_location(code="HUB", name="Hub")
    destination = build_location(code="DST", name="Destination")
    first_leg = build_provider_segment(
        origin=origin,
        destination=hub,
        transport_type=TransportType.train,
        departure_at=datetime(2026, 4, 14, 10, 50, tzinfo=MOSCOW_TZ),
        arrival_at=datetime(2026, 4, 14, 11, 14, tzinfo=MOSCOW_TZ),
        segment_code="6077",
        carrier_name="Severo-Kavkazskaya PPK",
        carrier_code="1369",
    )
    short_tail = build_provider_segment(
        origin=hub,
        destination=destination,
        transport_type=TransportType.train,
        departure_at=datetime(2026, 4, 14, 11, 41, tzinfo=MOSCOW_TZ),
        arrival_at=datetime(2026, 4, 14, 12, 59, tzinfo=MOSCOW_TZ),
        segment_code="6504",
        carrier_name="Severo-Kavkazskaya PPK",
        carrier_code="1369",
    )
    long_tail = build_provider_segment(
        origin=hub,
        destination=destination,
        transport_type=TransportType.train,
        departure_at=datetime(2026, 4, 14, 12, 24, tzinfo=MOSCOW_TZ),
        arrival_at=datetime(2026, 4, 14, 14, 16, tzinfo=MOSCOW_TZ),
        segment_code="6078",
        carrier_name="Severo-Kavkazskaya PPK",
        carrier_code="1369",
    )

    short_route = build_candidate(
        source="yandex_rasp_api",
        transfers=1,
        segment_ids=(first_leg.segment_id, short_tail.segment_id),
        total_duration_minutes=129,
        resolved_segments=(first_leg, short_tail),
    )
    long_route = build_candidate(
        source="yandex_rasp_api",
        transfers=1,
        segment_ids=(first_leg.segment_id, long_tail.segment_id),
        total_duration_minutes=206,
        resolved_segments=(first_leg, long_tail),
    )

    orchestrator = RouteSearchOrchestrator(
        [_StubExternalAdapter([short_route, long_route])]
    )
    results = await orchestrator.search(_build_criteria(max_transfers=2))

    assert {candidate.segment_ids[-1] for candidate in results} == {
        short_tail.segment_id,
        long_tail.segment_id,
    }


@pytest.mark.asyncio
async def test_orchestrator_prunes_earlier_transfer_route_with_same_tail() -> None:
    origin = build_location(code="ORI", name="Origin")
    hub = build_location(code="HUB", name="Hub")
    destination = build_location(code="DST", name="Destination")
    final_destination = build_location(code="FIN", name="Final")
    early_feeder = build_provider_segment(
        origin=origin,
        destination=hub,
        transport_type=TransportType.train,
        departure_at=datetime(2026, 4, 14, 12, 11, tzinfo=MOSCOW_TZ),
        arrival_at=datetime(2026, 4, 14, 12, 35, tzinfo=MOSCOW_TZ),
        segment_code="6079",
        carrier_name="Severo-Kavkazskaya PPK",
        carrier_code="1369",
    )
    late_feeder = build_provider_segment(
        origin=origin,
        destination=hub,
        transport_type=TransportType.train,
        departure_at=datetime(2026, 4, 14, 15, 6, tzinfo=MOSCOW_TZ),
        arrival_at=datetime(2026, 4, 14, 15, 38, tzinfo=MOSCOW_TZ),
        segment_code="6525",
        carrier_name="Severo-Kavkazskaya PPK",
        carrier_code="1369",
    )
    shared_middle = build_provider_segment(
        origin=hub,
        destination=destination,
        transport_type=TransportType.train,
        departure_at=datetime(2026, 4, 14, 17, 25, tzinfo=MOSCOW_TZ),
        arrival_at=datetime(2026, 4, 15, 3, 50, tzinfo=MOSCOW_TZ),
        segment_code="007A",
        carrier_name="Grand Service Express",
        carrier_code="63438",
    )
    shared_final = build_provider_segment(
        origin=destination,
        destination=final_destination,
        transport_type=TransportType.train,
        departure_at=datetime(2026, 4, 15, 5, 20, tzinfo=MOSCOW_TZ),
        arrival_at=datetime(2026, 4, 15, 6, 52, tzinfo=MOSCOW_TZ),
        segment_code="6617",
        carrier_name="Southern PPK",
        carrier_code="63578",
    )
    early_route = build_candidate(
        source="yandex_rasp_api",
        transfers=2,
        segment_ids=(
            early_feeder.segment_id,
            shared_middle.segment_id,
            shared_final.segment_id,
        ),
        total_duration_minutes=1121,
        resolved_segments=(early_feeder, shared_middle, shared_final),
    )
    late_route = build_candidate(
        source="yandex_rasp_api",
        transfers=2,
        segment_ids=(
            late_feeder.segment_id,
            shared_middle.segment_id,
            shared_final.segment_id,
        ),
        total_duration_minutes=946,
        resolved_segments=(late_feeder, shared_middle, shared_final),
    )

    orchestrator = RouteSearchOrchestrator(
        [_StubExternalAdapter([early_route, late_route])]
    )
    results = await orchestrator.search(_build_criteria(max_transfers=3))

    assert [candidate.segment_ids[0] for candidate in results] == [
        late_feeder.segment_id,
    ]


@pytest.mark.asyncio
async def test_orchestrator_builds_graph_candidates_from_external_segments() -> None:
    origin = build_location(code="ORI", name="Origin")
    hub = build_location(code="HUB", name="Hub")
    destination = build_location(code="DST", name="Destination")
    criteria = build_search_criteria(
        origin_id=origin.id,
        origin_type=origin.location_type,
        destination_id=destination.id,
        destination_type=destination.location_type,
        max_transfers=2,
    )

    first_leg = build_provider_segment(
        origin=origin,
        destination=hub,
        transport_type=TransportType.plane,
        departure_at=datetime(2026, 4, 14, 7, 0, tzinfo=MOSCOW_TZ),
        arrival_at=datetime(2026, 4, 14, 8, 0, tzinfo=MOSCOW_TZ),
        segment_code="S7 101",
        carrier_name="S7",
        carrier_code="S7",
    )
    second_leg = build_provider_segment(
        origin=hub,
        destination=destination,
        transport_type=TransportType.train,
        departure_at=datetime(2026, 4, 14, 10, 30, tzinfo=MOSCOW_TZ),
        arrival_at=datetime(2026, 4, 14, 12, 0, tzinfo=MOSCOW_TZ),
        segment_code="RZD 202",
        carrier_name="Russian Railways",
        carrier_code="RZD",
    )
    alt_first_leg = build_provider_segment(
        origin=origin,
        destination=hub,
        transport_type=TransportType.plane,
        departure_at=datetime(2026, 4, 14, 8, 0, tzinfo=MOSCOW_TZ),
        arrival_at=datetime(2026, 4, 14, 9, 0, tzinfo=MOSCOW_TZ),
        segment_code="SU 303",
        carrier_name="Aeroflot",
        carrier_code="SU",
    )
    alt_second_leg = build_provider_segment(
        origin=hub,
        destination=destination,
        transport_type=TransportType.train,
        departure_at=datetime(2026, 4, 14, 9, 45, tzinfo=MOSCOW_TZ),
        arrival_at=datetime(2026, 4, 14, 11, 15, tzinfo=MOSCOW_TZ),
        segment_code="RZD 404",
        carrier_name="Russian Railways",
        carrier_code="RZD",
    )

    external_adapter = _StubExternalAdapter(
        results=[
            build_candidate(
                source="yandex_rasp_api",
                transfers=1,
                segment_ids=(first_leg.segment_id, second_leg.segment_id),
                resolved_segments=(first_leg, second_leg),
            ),
            build_candidate(
                source="rzd_api",
                transfers=1,
                segment_ids=(alt_first_leg.segment_id, alt_second_leg.segment_id),
                resolved_segments=(alt_first_leg, alt_second_leg),
            ),
        ]
    )

    orchestrator = RouteSearchOrchestrator([external_adapter])
    results = await orchestrator.search(criteria)

    assert any(
        candidate.source == "graph_search"
        and candidate.segment_ids == (alt_first_leg.segment_id, second_leg.segment_id)
        and candidate.transfers == 1
        for candidate in results
    )


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
