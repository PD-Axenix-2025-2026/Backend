from __future__ import annotations

from datetime import date, datetime, time, timedelta
from uuid import NAMESPACE_URL, UUID, uuid5

from app.models.carrier import Carrier
from app.models.location import Location
from app.models.route_segment import RouteSegment
from app.seeding.catalog import (
    CARRIER_SEEDS,
    LOCATION_SEEDS,
    SEED_DATE_OFFSETS,
    SEED_TIMEZONE,
    SEGMENT_SEEDS,
)


def build_locations() -> dict[str, Location]:
    return {
        seed.key: Location(
            id=stable_uuid(f"location:{seed.key}"),
            code=seed.code,
            name=seed.name,
            city_name=seed.city_name,
            country_code=seed.country_code,
            location_type=seed.location_type,
            lat=seed.lat,
            lon=seed.lon,
            timezone=seed.timezone,
            is_hub=seed.is_hub,
            parent_location_id=resolve_parent_location_id(seed.parent_key),
        )
        for seed in LOCATION_SEEDS
    }


def build_carriers() -> dict[str, Carrier]:
    return {
        seed.key: Carrier(
            id=stable_uuid(f"carrier:{seed.key}"),
            code=seed.code,
            name=seed.name,
            transport_type=seed.transport_type,
            website_url=seed.website_url,
            is_active=True,
        )
        for seed in CARRIER_SEEDS
    }


def build_route_segments(
    *,
    base_date: date,
    locations: dict[str, Location],
    carriers: dict[str, Carrier],
) -> list[RouteSegment]:
    route_segments: list[RouteSegment] = []
    for day_offset in SEED_DATE_OFFSETS:
        travel_date = base_date + timedelta(days=day_offset)
        for segment_seed in SEGMENT_SEEDS:
            departure_at = build_departure_at(
                travel_date=travel_date,
                departure_time=segment_seed.departure_time,
            )
            arrival_at = departure_at + timedelta(
                minutes=segment_seed.duration_minutes,
            )
            route_segments.append(
                RouteSegment(
                    id=stable_uuid(
                        f"segment:{segment_seed.key}:{travel_date.isoformat()}"
                    ),
                    origin_location_id=locations[segment_seed.origin_key].id,
                    destination_location_id=locations[segment_seed.destination_key].id,
                    carrier_id=carriers[segment_seed.carrier_key].id,
                    transport_type=segment_seed.transport_type,
                    segment_code=segment_seed.segment_code,
                    departure_at=departure_at,
                    arrival_at=arrival_at,
                    duration_minutes=segment_seed.duration_minutes,
                    price_amount=segment_seed.price_amount,
                    currency_code="RUB",
                    available_seats=segment_seed.available_seats,
                    source_system="mock_seed",
                    source_record_id=(f"{segment_seed.key}:{travel_date.isoformat()}"),
                    is_active=True,
                    valid_from=departure_at - timedelta(days=14),
                    valid_to=None,
                )
            )
    return route_segments


def build_departure_at(*, travel_date: date, departure_time: time) -> datetime:
    return datetime.combine(
        travel_date,
        departure_time,
        tzinfo=SEED_TIMEZONE,
    )


def resolve_parent_location_id(parent_key: str | None) -> UUID | None:
    if parent_key is None:
        return None
    return stable_uuid(f"location:{parent_key}")


def stable_uuid(value: str) -> UUID:
    return uuid5(NAMESPACE_URL, f"pdaxenix:{value}")


__all__ = [
    "build_carriers",
    "build_departure_at",
    "build_locations",
    "build_route_segments",
    "resolve_parent_location_id",
    "stable_uuid",
]
