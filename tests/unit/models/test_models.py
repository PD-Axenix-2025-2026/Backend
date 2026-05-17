from datetime import UTC, datetime, timedelta
from decimal import Decimal
from uuid import uuid4

from app.models.base import Base
from app.models.carrier import Carrier
from app.models.enums import LocationType, TransportType
from app.models.location import Location
from app.models.route_segment import RouteSegment
from sqlalchemy import Uuid


def test_metadata_registers_graph_tables() -> None:
    assert {"locations", "carriers", "route_segments"} <= set(Base.metadata.tables)


def test_models_define_expected_foreign_keys_and_uuid_ids() -> None:
    assert isinstance(Location.__table__.c.id.type, Uuid)
    assert isinstance(Carrier.__table__.c.id.type, Uuid)
    assert isinstance(RouteSegment.__table__.c.id.type, Uuid)

    assert {
        foreign_key.target_fullname
        for foreign_key in Location.__table__.c.parent_location_id.foreign_keys
    } == {"locations.id"}
    assert {
        foreign_key.target_fullname
        for foreign_key in RouteSegment.__table__.c.origin_location_id.foreign_keys
    } == {"locations.id"}
    assert {
        foreign_key.target_fullname
        for foreign_key in RouteSegment.__table__.c.destination_location_id.foreign_keys
    } == {"locations.id"}
    assert {
        foreign_key.target_fullname
        for foreign_key in RouteSegment.__table__.c.carrier_id.foreign_keys
    } == {"carriers.id"}

    assert Location.__table__.c.location_type.nullable is False
    assert Carrier.__table__.c.transport_type.nullable is False
    assert RouteSegment.__table__.c.transport_type.nullable is False


def test_route_segment_duration_is_calculated_in_model() -> None:
    departure_at = datetime(2026, 3, 25, 8, 30, tzinfo=UTC)
    arrival_at = departure_at + timedelta(hours=2, minutes=45)

    segment = RouteSegment(
        origin_location_id=uuid4(),
        destination_location_id=uuid4(),
        carrier_id=uuid4(),
        transport_type=TransportType.train,
        segment_code="A100",
        departure_at=departure_at,
        arrival_at=arrival_at,
        duration_minutes=0,
        price_amount=Decimal("1499.99"),
        currency_code="RUB",
    )

    segment.sync_duration_minutes()

    assert segment.duration_minutes == 165


def test_route_segment_duration_listener_uses_same_sync_method() -> None:
    departure_at = datetime(2026, 3, 25, 10, 0, tzinfo=UTC)
    arrival_at = departure_at + timedelta(minutes=95)

    segment = RouteSegment(
        origin_location_id=uuid4(),
        destination_location_id=uuid4(),
        carrier_id=uuid4(),
        transport_type=TransportType.bus,
        departure_at=departure_at,
        arrival_at=arrival_at,
        duration_minutes=0,
        price_amount=Decimal("799.00"),
        currency_code="RUB",
    )

    segment.sync_duration_minutes()

    assert segment.duration_minutes == 95


def test_location_and_carrier_enums_store_expected_values() -> None:
    assert LocationType.city.value == "city"
    assert LocationType.railway_station.value == "railway_station"
    assert TransportType.plane.value == "plane"
    assert TransportType.bus.value == "bus"
