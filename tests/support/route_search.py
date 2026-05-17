from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal
from uuid import UUID, uuid4

from app.models.carrier import Carrier
from app.models.enums import LocationType, TransportType
from app.models.location import Location
from app.models.route_segment import RouteSegment
from app.services.search.contracts import (
    PassengerCounts,
    ProviderRouteSegment,
    RouteCandidate,
    RouteSearchCriteria,
    RouteSearchPreferences,
)

DEFAULT_TRAVEL_DATE = date(2026, 4, 14)
MOSCOW_TZ = timezone(timedelta(hours=3))
TEST_CURRENCY = "RUB"


@dataclass(slots=True, frozen=True)
class TransferLocationSet:
    origin_city: Location
    origin_airport: Location
    hub_city: Location
    hub_station: Location
    hub_airport: Location
    destination_city: Location
    destination_airport: Location


def build_transfer_location_set() -> TransferLocationSet:
    origin_city = build_location(
        code="MOW",
        name="Moscow",
        location_type=LocationType.city,
    )
    origin_airport = build_location(
        code="SVO",
        name="Sheremetyevo",
        location_type=LocationType.airport,
        parent_location_id=origin_city.id,
        city_name="Moscow",
    )
    hub_city = build_location(
        code="KZN",
        name="Kazan",
        location_type=LocationType.city,
    )
    hub_station = build_location(
        code="KZN-1",
        name="Kazan Station",
        location_type=LocationType.railway_station,
        parent_location_id=hub_city.id,
        city_name="Kazan",
    )
    hub_airport = build_location(
        code="KZN-2",
        name="Kazan Airport",
        location_type=LocationType.airport,
        parent_location_id=hub_city.id,
        city_name="Kazan",
    )
    destination_city = build_location(
        code="SPB",
        name="Saint Petersburg",
        location_type=LocationType.city,
    )
    destination_airport = build_location(
        code="LED",
        name="Pulkovo",
        location_type=LocationType.airport,
        parent_location_id=destination_city.id,
        city_name="Saint Petersburg",
    )
    return TransferLocationSet(
        origin_city=origin_city,
        origin_airport=origin_airport,
        hub_city=hub_city,
        hub_station=hub_station,
        hub_airport=hub_airport,
        destination_city=destination_city,
        destination_airport=destination_airport,
    )


def build_search_criteria(
    *,
    origin_id: UUID,
    origin_type: LocationType,
    destination_id: UUID,
    destination_type: LocationType,
    max_transfers: int | None,
    travel_date: date = DEFAULT_TRAVEL_DATE,
) -> RouteSearchCriteria:
    return RouteSearchCriteria(
        origin_id=origin_id,
        origin_type=origin_type,
        destination_id=destination_id,
        destination_type=destination_type,
        travel_date=travel_date,
        passengers=PassengerCounts(),
        preferences=RouteSearchPreferences(max_transfers=max_transfers),
    )


def build_candidate(
    *,
    source: str,
    transfers: int,
    segment_count: int = 1,
    segment_ids: tuple[UUID, ...] | None = None,
    total_price: Decimal | None = Decimal("1000.00"),
    total_duration_minutes: int | None = None,
    resolved_segments: tuple[RouteSegment | ProviderRouteSegment, ...] = (),
) -> RouteCandidate:
    if segment_ids is None:
        segment_ids = tuple(uuid4() for _ in range(segment_count))
    return RouteCandidate(
        source=source,
        segment_ids=segment_ids,
        total_price=total_price,
        total_duration_minutes=(
            total_duration_minutes
            if total_duration_minutes is not None
            else 120 * max(len(segment_ids), 1)
        ),
        transfers=transfers,
        resolved_segments=resolved_segments,
    )


def build_location(
    *,
    code: str,
    name: str,
    location_type: LocationType = LocationType.city,
    parent_location_id: UUID | None = None,
    city_name: str | None = None,
    provider_code: str | None = None,
) -> Location:
    return Location(
        id=uuid4(),
        code=code,
        rzd_code=provider_code if provider_code and provider_code.isdigit() else None,
        yandex_code=(
            provider_code if provider_code and not provider_code.isdigit() else None
        ),
        name=name,
        city_name=city_name or name,
        country_code="RU",
        location_type=location_type,
        timezone="Europe/Moscow",
        parent_location_id=parent_location_id,
    )


def build_carrier(
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


def build_db_segment(
    *,
    origin: Location,
    destination: Location,
    transport_type: TransportType,
    departure_at: datetime,
    arrival_at: datetime,
    price_amount: Decimal,
    segment_code: str,
    carrier: Carrier | None = None,
    source_system: str = "database",
    source_record_id: str | None = None,
    available_seats: int = 10,
    valid_from: datetime | None = None,
    valid_to: datetime | None = None,
) -> RouteSegment:
    segment_carrier = carrier or build_carrier(
        code=f"{transport_type.value[:2].upper()}-{segment_code}",
        name=f"{transport_type.value.title()} Carrier",
        transport_type=transport_type,
    )
    return RouteSegment(
        id=uuid4(),
        origin_location_id=origin.id,
        destination_location_id=destination.id,
        carrier_id=segment_carrier.id,
        transport_type=transport_type,
        segment_code=segment_code,
        departure_at=departure_at,
        arrival_at=arrival_at,
        duration_minutes=int((arrival_at - departure_at).total_seconds() // 60),
        price_amount=price_amount,
        currency_code=TEST_CURRENCY,
        available_seats=available_seats,
        source_system=source_system,
        source_record_id=source_record_id or segment_code,
        is_active=True,
        valid_from=valid_from or departure_at - timedelta(days=1),
        valid_to=valid_to,
        origin_location=origin,
        destination_location=destination,
        carrier=segment_carrier,
    )


def build_provider_segment(
    *,
    origin: Location,
    destination: Location,
    transport_type: TransportType,
    departure_at: datetime,
    arrival_at: datetime,
    segment_code: str,
    carrier_name: str,
    carrier_code: str | None,
    price_amount: Decimal | None = None,
    source_system: str = "provider",
    source_record_id: str | None = None,
    available_seats: int | None = None,
    valid_from: datetime | None = None,
    valid_to: datetime | None = None,
) -> ProviderRouteSegment:
    return ProviderRouteSegment(
        segment_id=uuid4(),
        transport_type=transport_type,
        carrier_name=carrier_name,
        carrier_code=carrier_code,
        segment_code=segment_code,
        origin_location=origin,
        destination_location=destination,
        departure_at=departure_at,
        arrival_at=arrival_at,
        duration_minutes=int((arrival_at - departure_at).total_seconds() // 60),
        price_amount=price_amount,
        currency_code=TEST_CURRENCY,
        available_seats=available_seats,
        source_system=source_system,
        source_record_id=source_record_id or segment_code,
        valid_from=valid_from or departure_at - timedelta(days=7),
        valid_to=valid_to,
    )
