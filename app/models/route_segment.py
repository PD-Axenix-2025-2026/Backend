from datetime import date, datetime
from decimal import Decimal

from sqlalchemy import Date, DateTime, ForeignKey, Numeric, String
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.models.base import Base, TimestampMixin
from app.models.location import Location


class RouteSegment(TimestampMixin, Base):
    __tablename__ = "route_segments"

    id: Mapped[int] = mapped_column(primary_key=True)
    origin_id: Mapped[int] = mapped_column(ForeignKey("locations.id"), index=True)
    destination_id: Mapped[int] = mapped_column(ForeignKey("locations.id"), index=True)
    transport_type: Mapped[str] = mapped_column(String(32), index=True)
    departure_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)
    arrival_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    travel_date: Mapped[date] = mapped_column(Date, index=True)
    price: Mapped[Decimal] = mapped_column(Numeric(10, 2))
    currency: Mapped[str] = mapped_column(String(3), default="RUB")

    origin: Mapped[Location] = relationship(
        Location,
        foreign_keys=[origin_id],
        lazy="joined",
    )
    destination: Mapped[Location] = relationship(
        Location,
        foreign_keys=[destination_id],
        lazy="joined",
    )
