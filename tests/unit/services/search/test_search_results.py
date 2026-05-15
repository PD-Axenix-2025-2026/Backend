from __future__ import annotations

from datetime import datetime, timedelta, timezone
from decimal import Decimal
from uuid import uuid4

from app.models.enums import TransportType
from app.services.search.contracts import SearchResultsQuery, SearchSortOption
from app.services.search.results import (
    build_effective_results_query,
    build_price_range,
    collect_visible_routes,
)
from app.services.search.store.models import (
    MoneySnapshot,
    RouteSegmentSnapshot,
    RouteSnapshot,
)

from tests.support.search_service import build_search_fixture

MOSCOW_TZ = timezone(timedelta(hours=3))


def test_unknown_total_price_routes_sort_last_and_skip_price_range() -> None:
    fixture = build_search_fixture()
    priced_route = _build_route_snapshot(
        transport_type=TransportType.train,
        departure_at=datetime(2026, 4, 14, 8, 0, tzinfo=MOSCOW_TZ),
        arrival_at=datetime(2026, 4, 14, 12, 0, tzinfo=MOSCOW_TZ),
        total_price=MoneySnapshot(amount=Decimal("4500.00"), currency="RUB"),
    )
    unknown_price_route = _build_route_snapshot(
        transport_type=TransportType.plane,
        departure_at=datetime(2026, 4, 14, 7, 45, tzinfo=MOSCOW_TZ),
        arrival_at=datetime(2026, 4, 14, 9, 25, tzinfo=MOSCOW_TZ),
        total_price=None,
    )

    effective_query = build_effective_results_query(
        fixture.criteria,
        SearchResultsQuery(sort=SearchSortOption.price),
    )
    visible_routes = collect_visible_routes(
        [unknown_price_route, priced_route],
        effective_query,
    )

    assert visible_routes == [priced_route, unknown_price_route]
    assert build_price_range(visible_routes).min == Decimal("4500.00")
    assert build_price_range(visible_routes).max == Decimal("4500.00")


def test_unknown_total_price_routes_do_not_pass_max_price_filter() -> None:
    fixture = build_search_fixture()
    priced_route = _build_route_snapshot(
        transport_type=TransportType.train,
        departure_at=datetime(2026, 4, 14, 8, 0, tzinfo=MOSCOW_TZ),
        arrival_at=datetime(2026, 4, 14, 12, 0, tzinfo=MOSCOW_TZ),
        total_price=MoneySnapshot(amount=Decimal("4500.00"), currency="RUB"),
    )
    unknown_price_route = _build_route_snapshot(
        transport_type=TransportType.plane,
        departure_at=datetime(2026, 4, 14, 7, 45, tzinfo=MOSCOW_TZ),
        arrival_at=datetime(2026, 4, 14, 9, 25, tzinfo=MOSCOW_TZ),
        total_price=None,
    )

    effective_query = build_effective_results_query(
        fixture.criteria,
        SearchResultsQuery(max_price=Decimal("5000.00")),
    )
    visible_routes = collect_visible_routes(
        [unknown_price_route, priced_route],
        effective_query,
    )

    assert visible_routes == [priced_route]


def _build_route_snapshot(
    *,
    transport_type: TransportType,
    departure_at: datetime,
    arrival_at: datetime,
    total_price: MoneySnapshot | None,
) -> RouteSnapshot:
    route_id = uuid4()
    search_id = uuid4()
    segment_id = uuid4()
    origin_id = uuid4()
    destination_id = uuid4()
    segment = RouteSegmentSnapshot(
        segment_id=segment_id,
        transport_type=transport_type,
        carrier="Carrier",
        carrier_code="C1",
        segment_code="SEG-1",
        origin_id=origin_id,
        origin_code="ORI",
        origin_label="Origin",
        destination_id=destination_id,
        destination_code="DST",
        destination_label="Destination",
        departure_at=departure_at,
        arrival_at=arrival_at,
        duration_minutes=int((arrival_at - departure_at).total_seconds() // 60),
        price=total_price,
        available_seats=10,
        source_system="provider",
        source_record_id="provider-1",
        valid_from=departure_at - timedelta(days=1),
        valid_to=None,
    )
    return RouteSnapshot(
        route_id=route_id,
        search_id=search_id,
        source="provider",
        segment_ids=(segment_id,),
        departure_at=departure_at,
        arrival_at=arrival_at,
        duration_minutes=segment.duration_minutes,
        transfers=0,
        total_price=total_price,
        transport_types=(transport_type,),
        segments=(segment,),
    )
