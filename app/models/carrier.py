from __future__ import annotations

from typing import TYPE_CHECKING

from sqlalchemy import Boolean, Enum, String
from sqlalchemy import true as sql_true
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.models.base import Base, UUIDPrimaryKeyMixin
from app.models.enums import TransportType

if TYPE_CHECKING:
    from app.models.route_segment import RouteSegment


class Carrier(UUIDPrimaryKeyMixin, Base):
    __tablename__ = "carriers"

    # Carrier identity and capabilities
    code: Mapped[str | None] = mapped_column(String(64), index=True)
    name: Mapped[str] = mapped_column(String(255), nullable=False, index=True)
    transport_type: Mapped[TransportType] = mapped_column(
        Enum(TransportType, name="transport_type_enum", native_enum=False),
        nullable=False,
        index=True,
    )
    website_url: Mapped[str | None] = mapped_column(String(512))
    is_active: Mapped[bool] = mapped_column(
        Boolean,
        default=True,
        server_default=sql_true(),
        nullable=False,
    )

    # Graph relationships
    route_segments: Mapped[list[RouteSegment]] = relationship(
        back_populates="carrier",
    )
