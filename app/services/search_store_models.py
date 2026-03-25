from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from decimal import Decimal
from uuid import UUID

from app.models.enums import TransportType
from app.services.contracts import RouteSearchCriteria, SearchStatus


def utc_now() -> datetime:
    return datetime.now(UTC)


class SearchNotFoundError(LookupError):
    pass


class RouteNotFoundError(LookupError):
    pass


@dataclass(slots=True, frozen=True)
class MoneySnapshot:
    amount: Decimal
    currency: str


@dataclass(slots=True, frozen=True)
class RouteSegmentSnapshot:
    segment_id: UUID
    transport_type: TransportType
    carrier: str
    carrier_code: str | None
    segment_code: str | None
    origin_id: UUID
    origin_code: str | None
    origin_label: str
    destination_id: UUID
    destination_code: str | None
    destination_label: str
    departure_at: datetime
    arrival_at: datetime
    duration_minutes: int
    price: MoneySnapshot
    available_seats: int | None
    source_system: str | None
    source_record_id: str | None
    valid_from: datetime
    valid_to: datetime | None


@dataclass(slots=True, frozen=True)
class RouteSnapshot:
    route_id: UUID
    search_id: UUID
    source: str
    segment_ids: tuple[UUID, ...]
    departure_at: datetime
    arrival_at: datetime
    duration_minutes: int
    transfers: int
    total_price: MoneySnapshot
    transport_types: tuple[TransportType, ...]
    segments: tuple[RouteSegmentSnapshot, ...]
    booking_available: bool = True
    refresh_required: bool = False

    @property
    def base_labels(self) -> tuple[str, ...]:
        if self.transfers == 0:
            return ("direct",)
        return ()


@dataclass(slots=True)
class SearchRecord:
    search_id: UUID
    criteria: RouteSearchCriteria
    status: SearchStatus
    created_at: datetime
    updated_at: datetime
    expires_at: datetime
    last_update: int
    routes: tuple[RouteSnapshot, ...] = ()
    error_message: str | None = None

    def mark_complete(
        self,
        *,
        routes: tuple[RouteSnapshot, ...],
        updated_at: datetime,
    ) -> None:
        self.routes = routes
        self.status = SearchStatus.complete
        self.updated_at = updated_at
        self.last_update += 1
        self.error_message = None

    def mark_failed(
        self,
        *,
        error_message: str,
        updated_at: datetime,
    ) -> None:
        self.status = SearchStatus.failed
        self.updated_at = updated_at
        self.last_update += 1
        self.error_message = error_message


__all__ = [
    "MoneySnapshot",
    "RouteNotFoundError",
    "RouteSegmentSnapshot",
    "RouteSnapshot",
    "SearchNotFoundError",
    "SearchRecord",
    "utc_now",
]
