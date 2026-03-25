from __future__ import annotations

from datetime import datetime
from decimal import Decimal
from typing import TYPE_CHECKING
from uuid import UUID

from sqlalchemy import (
    CHAR,
    Boolean,
    DateTime,
    Enum,
    ForeignKey,
    Integer,
    Numeric,
    String,
    event,
    func,
)
from sqlalchemy import (
    true as sql_true,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.models.base import Base, TimestampMixin, UUIDPrimaryKeyMixin
from app.models.enums import TransportType

if TYPE_CHECKING:
    from app.models.carrier import Carrier
    from app.models.location import Location

LOCATION_ID_FK = "locations.id"
CARRIER_ID_FK = "carriers.id"
TRANSPORT_TYPE_DB_ENUM = Enum(
    TransportType,
    name="transport_type_enum",
    native_enum=False,
)
TIMESTAMPTZ = DateTime(timezone=True)


def _calculate_duration_minutes(departure_at: datetime, arrival_at: datetime) -> int:
    return int((arrival_at - departure_at).total_seconds() // 60)


class RouteSegment(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    __tablename__ = "route_segments"

    # Graph edges
    origin_location_id: Mapped[UUID] = mapped_column(
        ForeignKey(LOCATION_ID_FK),
        nullable=False,
        index=True,
    )
    destination_location_id: Mapped[UUID] = mapped_column(
        ForeignKey(LOCATION_ID_FK),
        nullable=False,
        index=True,
    )
    carrier_id: Mapped[UUID] = mapped_column(
        ForeignKey(CARRIER_ID_FK),
        nullable=False,
        index=True,
    )

    # Transport identity
    transport_type: Mapped[TransportType] = mapped_column(
        TRANSPORT_TYPE_DB_ENUM,
        nullable=False,
        index=True,
    )
    segment_code: Mapped[str | None] = mapped_column(String(64), index=True)

    # Timing
    departure_at: Mapped[datetime] = mapped_column(TIMESTAMPTZ, nullable=False)
    arrival_at: Mapped[datetime] = mapped_column(TIMESTAMPTZ, nullable=False)
    duration_minutes: Mapped[int] = mapped_column(Integer, nullable=False)

    # Pricing and capacity
    price_amount: Mapped[Decimal] = mapped_column(Numeric(12, 2), nullable=False)
    currency_code: Mapped[str] = mapped_column(
        CHAR(3),
        nullable=False,
        default="RUB",
        server_default="RUB",
    )
    available_seats: Mapped[int | None] = mapped_column(Integer)

    # Source metadata and lifecycle
    source_system: Mapped[str | None] = mapped_column(String(64))
    source_record_id: Mapped[str | None] = mapped_column(String(255))
    is_active: Mapped[bool] = mapped_column(
        Boolean,
        default=True,
        server_default=sql_true(),
        nullable=False,
    )
    valid_from: Mapped[datetime] = mapped_column(
        TIMESTAMPTZ,
        server_default=func.now(),
        nullable=False,
    )
    valid_to: Mapped[datetime | None] = mapped_column(TIMESTAMPTZ)

    # Relationships
    origin_location: Mapped[Location] = relationship(
        "Location",
        foreign_keys=[origin_location_id],
        back_populates="outbound_segments",
        lazy="joined",
    )
    destination_location: Mapped[Location] = relationship(
        "Location",
        foreign_keys=[destination_location_id],
        back_populates="inbound_segments",
        lazy="joined",
    )
    carrier: Mapped[Carrier] = relationship(
        "Carrier",
        back_populates="route_segments",
        lazy="joined",
    )

    def sync_duration_minutes(self) -> None:
        self.duration_minutes = _calculate_duration_minutes(
            departure_at=self.departure_at,
            arrival_at=self.arrival_at,
        )


@event.listens_for(RouteSegment, "before_insert")
@event.listens_for(RouteSegment, "before_update")
def _sync_duration_minutes_before_save(
    _mapper: object,
    _connection: object,
    target: RouteSegment,
) -> None:
    target.sync_duration_minutes()
