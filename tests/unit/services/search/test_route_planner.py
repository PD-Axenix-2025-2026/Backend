from __future__ import annotations

from collections.abc import Sequence
from datetime import datetime
from decimal import Decimal

from app.models.enums import TransportType
from app.models.route_segment import RouteSegment
from app.services.search.contracts import RouteCandidate
from app.services.search.planner import (
    EndpointScope,
    build_database_route_candidates,
    build_planner_constraints,
)

from tests.support.route_search import (
    MOSCOW_TZ,
    TransferLocationSet,
    build_db_segment,
    build_search_criteria,
    build_transfer_location_set,
)


def test_route_planner_builds_direct_and_same_city_transfer_candidates() -> None:
    locations = build_transfer_location_set()
    direct_segment = build_db_segment(
        origin=locations.origin_airport,
        destination=locations.destination_airport,
        transport_type=TransportType.plane,
        departure_at=datetime(2026, 4, 14, 8, 0, tzinfo=MOSCOW_TZ),
        arrival_at=datetime(2026, 4, 14, 9, 30, tzinfo=MOSCOW_TZ),
        price_amount=Decimal("4500.00"),
        segment_code="SU 100",
    )
    first_leg = build_db_segment(
        origin=locations.origin_airport,
        destination=locations.hub_station,
        transport_type=TransportType.plane,
        departure_at=datetime(2026, 4, 14, 7, 0, tzinfo=MOSCOW_TZ),
        arrival_at=datetime(2026, 4, 14, 8, 30, tzinfo=MOSCOW_TZ),
        price_amount=Decimal("2000.00"),
        segment_code="SU 200",
    )
    second_leg = build_db_segment(
        origin=locations.hub_airport,
        destination=locations.destination_airport,
        transport_type=TransportType.train,
        departure_at=datetime(2026, 4, 14, 10, 0, tzinfo=MOSCOW_TZ),
        arrival_at=datetime(2026, 4, 14, 12, 0, tzinfo=MOSCOW_TZ),
        price_amount=Decimal("1500.00"),
        segment_code="RZD 300",
    )

    candidates = _plan_routes(
        locations=locations,
        max_transfers=1,
        segments=(direct_segment, first_leg, second_leg),
    )

    assert [
        (candidate.segment_ids, candidate.transfers) for candidate in candidates
    ] == [
        ((direct_segment.id,), 0),
        ((first_leg.id, second_leg.id), 1),
    ]
    assert candidates[1].total_price == Decimal("3500.00")
    assert candidates[1].total_duration_minutes == 300


def test_route_planner_respects_layover_window_and_avoids_city_cycles() -> None:
    locations = build_transfer_location_set()
    first_leg = build_db_segment(
        origin=locations.origin_airport,
        destination=locations.hub_station,
        transport_type=TransportType.plane,
        departure_at=datetime(2026, 4, 14, 7, 0, tzinfo=MOSCOW_TZ),
        arrival_at=datetime(2026, 4, 14, 8, 0, tzinfo=MOSCOW_TZ),
        price_amount=Decimal("1000.00"),
        segment_code="SU 1",
    )
    too_short_layover = build_db_segment(
        origin=locations.hub_airport,
        destination=locations.destination_airport,
        transport_type=TransportType.train,
        departure_at=datetime(2026, 4, 14, 8, 59, tzinfo=MOSCOW_TZ),
        arrival_at=datetime(2026, 4, 14, 10, 0, tzinfo=MOSCOW_TZ),
        price_amount=Decimal("1000.00"),
        segment_code="RZD 2",
    )
    valid_layover = build_db_segment(
        origin=locations.hub_airport,
        destination=locations.destination_airport,
        transport_type=TransportType.train,
        departure_at=datetime(2026, 4, 14, 9, 0, tzinfo=MOSCOW_TZ),
        arrival_at=datetime(2026, 4, 14, 10, 15, tzinfo=MOSCOW_TZ),
        price_amount=Decimal("1200.00"),
        segment_code="RZD 3",
    )
    loop_back_to_origin_city = build_db_segment(
        origin=locations.hub_airport,
        destination=locations.origin_airport,
        transport_type=TransportType.train,
        departure_at=datetime(2026, 4, 14, 9, 30, tzinfo=MOSCOW_TZ),
        arrival_at=datetime(2026, 4, 14, 11, 30, tzinfo=MOSCOW_TZ),
        price_amount=Decimal("900.00"),
        segment_code="RZD 4",
    )

    candidates = _plan_routes(
        locations=locations,
        max_transfers=2,
        segments=(
            first_leg,
            too_short_layover,
            valid_layover,
            loop_back_to_origin_city,
        ),
    )

    assert [
        (candidate.segment_ids, candidate.transfers) for candidate in candidates
    ] == [((first_leg.id, valid_layover.id), 1)]


def _plan_routes(
    *,
    locations: TransferLocationSet,
    max_transfers: int | None,
    segments: Sequence[RouteSegment],
) -> list[RouteCandidate]:
    criteria = build_search_criteria(
        origin_id=locations.origin_city.id,
        origin_type=locations.origin_city.location_type,
        destination_id=locations.destination_city.id,
        destination_type=locations.destination_city.location_type,
        max_transfers=max_transfers,
    )
    return build_database_route_candidates(
        criteria=criteria,
        segments=segments,
        endpoint_scope=_build_endpoint_scope(locations),
        constraints=build_planner_constraints(criteria),
    )


def _build_endpoint_scope(locations: TransferLocationSet) -> EndpointScope:
    return EndpointScope(
        origin_endpoint_ids=frozenset(
            {locations.origin_city.id, locations.origin_airport.id}
        ),
        destination_endpoint_ids=frozenset(
            {locations.destination_city.id, locations.destination_airport.id}
        ),
    )
