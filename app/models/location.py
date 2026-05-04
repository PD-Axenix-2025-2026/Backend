from __future__ import annotations

from typing import TYPE_CHECKING
from uuid import UUID

from sqlalchemy import CHAR, Boolean, Enum, Float, ForeignKey, String
from sqlalchemy import false as sql_false
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.models.base import Base, UUIDPrimaryKeyMixin
from app.models.enums import LocationType

if TYPE_CHECKING:
    from app.models.route_segment import RouteSegment


class Location(UUIDPrimaryKeyMixin, Base):
    __tablename__ = "locations"

    # Location identity
    code: Mapped[str | None] = mapped_column(String(64), unique=True, index=True)
    rzd_code: Mapped[str | None] = mapped_column(String(64), index=True)
    yandex_code: Mapped[str | None] = mapped_column(String(64), unique=True, index=True)
    name: Mapped[str] = mapped_column(String(255), nullable=False, index=True)
    city_name: Mapped[str | None] = mapped_column(String(255), index=True)
    country_code: Mapped[str | None] = mapped_column(CHAR(2))
    location_type: Mapped[LocationType] = mapped_column(
        Enum(LocationType, name="location_type_enum", native_enum=False),
        nullable=False,
        index=True,
    )
    lat: Mapped[float | None] = mapped_column(Float(53))
    lon: Mapped[float | None] = mapped_column(Float(53))
    timezone: Mapped[str] = mapped_column(String(64), nullable=False)

    # Search and hierarchy behavior
    is_hub: Mapped[bool] = mapped_column(
        Boolean,
        default=False,
        server_default=sql_false(),
        nullable=False,
    )
    parent_location_id: Mapped[UUID | None] = mapped_column(
        ForeignKey("locations.id"),
        index=True,
    )

    # Hierarchy and graph relationships
    parent_location: Mapped[Location | None] = relationship(
        "Location",
        remote_side="Location.id",
        back_populates="child_locations",
    )
    child_locations: Mapped[list[Location]] = relationship(
        "Location",
        back_populates="parent_location",
    )
    outbound_segments: Mapped[list[RouteSegment]] = relationship(
        "RouteSegment",
        foreign_keys="RouteSegment.origin_location_id",
        back_populates="origin_location",
    )
    inbound_segments: Mapped[list[RouteSegment]] = relationship(
        "RouteSegment",
        foreign_keys="RouteSegment.destination_location_id",
        back_populates="destination_location",
    )
