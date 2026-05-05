from dataclasses import dataclass, field
from datetime import date
from decimal import Decimal
from enum import StrEnum
from uuid import UUID

from app.models.enums import LocationType, TransportType
from app.models.route_segment import RouteSegment


class SearchStatus(StrEnum):
    pending = "pending"
    partial = "partial"
    complete = "complete"
    failed = "failed"


class SearchSortOption(StrEnum):
    best = "best"
    price = "price"
    duration = "duration"


@dataclass(slots=True, frozen=True)
class PassengerCounts:
    adults: int = 1
    children: int = 0
    infants: int = 0

    @property
    def total(self) -> int:
        return self.adults + self.children + self.infants


@dataclass(slots=True, frozen=True)
class RouteSearchPreferences:
    sort: SearchSortOption = SearchSortOption.best
    max_transfers: int | None = None
    max_price: Decimal | None = None
    max_duration_minutes: int | None = None


@dataclass(slots=True, frozen=True)
class RouteSearchCriteria:
    origin_id: UUID
    origin_type: LocationType
    destination_id: UUID
    destination_type: LocationType
    travel_date: date
    passengers: PassengerCounts = field(default_factory=PassengerCounts)
    transport_types: tuple[TransportType, ...] = field(default_factory=tuple)
    preferences: RouteSearchPreferences = field(default_factory=RouteSearchPreferences)


@dataclass(slots=True, frozen=True)
class SearchResultsQuery:
    last_update: int = 0
    sort: SearchSortOption | None = None
    max_price: Decimal | None = None
    max_transfers: int | None = None
    max_duration_minutes: int | None = None
    transport_types: tuple[TransportType, ...] = field(default_factory=tuple)
    limit: int = 20
    offset: int = 0


@dataclass(slots=True, frozen=True)
class RouteCandidate:
    source: str
    segment_ids: tuple[UUID, ...]
    total_price: Decimal | None
    total_duration_minutes: int | None
    transfers: int = 0
    resolved_segments: tuple[RouteSegment, ...] = field(default_factory=tuple)
