from datetime import datetime
from uuid import UUID

from pydantic import BaseModel

from app.models.enums import TransportType


class MoneyResponse(BaseModel):
    amount: float
    currency: str


class RouteSummaryResponse(BaseModel):
    departure_at: datetime
    arrival_at: datetime
    duration_minutes: int
    transfers: int
    total_price: MoneyResponse


class RouteSegmentResponse(BaseModel):
    segment_id: UUID
    transport_type: TransportType
    carrier: str
    carrier_code: str | None = None
    segment_code: str | None = None
    origin_id: UUID
    origin_code: str | None = None
    origin_label: str
    destination_id: UUID
    destination_code: str | None = None
    destination_label: str
    departure_at: datetime
    arrival_at: datetime
    duration_minutes: int
    price: MoneyResponse
    available_seats: int | None = None
    source_system: str | None = None
    source_record_id: str | None = None
    valid_from: datetime
    valid_to: datetime | None = None


class RouteBookingResponse(BaseModel):
    available: bool
    refresh_required: bool


class RouteListItemResponse(BaseModel):
    route_id: UUID
    summary: RouteSummaryResponse
    segments: list[RouteSegmentResponse]
    labels: list[str]
    booking: RouteBookingResponse


class RouteDetailResponse(BaseModel):
    route_id: UUID
    search_id: UUID
    source: str
    segment_ids: list[UUID]
    summary: RouteSummaryResponse
    segments: list[RouteSegmentResponse]
    labels: list[str]
    booking: RouteBookingResponse


class CheckoutLinkRequest(BaseModel):
    provider_offer_id: str | None = None


class CheckoutLinkResponse(BaseModel):
    method: str
    url: str
    expires_at: datetime
    params: dict[str, str] | None = None
