from dataclasses import dataclass
from datetime import date
from decimal import Decimal
from uuid import UUID


@dataclass(slots=True, frozen=True)
class RouteSearchCriteria:
    origin_code: str
    destination_code: str
    travel_date: date
    passengers: int = 1


@dataclass(slots=True, frozen=True)
class RouteCandidate:
    source: str
    segment_ids: tuple[UUID, ...]
    total_price: Decimal | None
    total_duration_minutes: int | None
    transfers: int = 0
