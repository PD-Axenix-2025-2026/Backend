from __future__ import annotations

from collections import defaultdict
from datetime import date

from app.models.enums import LocationType
from app.models.route_segment import RouteSegment
from app.seeding.load_test_data import build_load_test_data_bundle
from app.seeding.mock_data import build_mock_data_bundle

BASE_DATE = date(2026, 5, 22)


def test_build_load_test_data_bundle_generates_transfer_chains() -> None:
    mock_bundle = build_mock_data_bundle(BASE_DATE)
    bundle = build_load_test_data_bundle(
        base_date=BASE_DATE,
        days=4,
        target_segments=240,
        random_seed=123,
        locations=mock_bundle.locations,
        carriers=mock_bundle.carriers,
    )

    segments_by_chain: dict[str, list[RouteSegment]] = defaultdict(list)
    for segment in bundle.route_segments:
        assert segment.departure_at.date() >= BASE_DATE
        assert segment.source_system == "load_test_seed"
        chain_key = segment.source_record_id.rsplit(":", 1)[0]
        segments_by_chain[chain_key].append(segment)

    transfer_chains = [
        chain_segments
        for chain_key, chain_segments in segments_by_chain.items()
        if ":transfer:" in chain_key and len(chain_segments) == 2
    ]

    assert len(bundle.route_segments) == 240
    assert bundle.base_date == BASE_DATE
    assert bundle.transfer_routes > 0
    assert transfer_chains

    first_leg, second_leg = transfer_chains[0]
    layover_minutes = int(
        (second_leg.departure_at - first_leg.arrival_at).total_seconds() // 60
    )
    assert first_leg.destination_location_id == second_leg.origin_location_id
    assert 60 <= layover_minutes <= 720
    assert first_leg.departure_at.date() == BASE_DATE
    assert second_leg.departure_at.date() >= BASE_DATE

    assert {location.location_type for location in mock_bundle.locations} == {
        LocationType.city,
        LocationType.airport,
        LocationType.railway_station,
        LocationType.bus_station,
    }