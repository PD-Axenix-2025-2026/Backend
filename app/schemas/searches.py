from datetime import date, datetime
from decimal import Decimal
from uuid import UUID

from pydantic import BaseModel, Field, model_validator

from app.models.enums import LocationType, TransportType
from app.schemas.routes import RouteListItemResponse
from app.services.contracts import SearchSortOption, SearchStatus


class SearchLocationReference(BaseModel):
    id: UUID
    type: LocationType


class PassengerCountsRequest(BaseModel):
    adults: int = Field(default=1, ge=1)
    children: int = Field(default=0, ge=0)
    infants: int = Field(default=0, ge=0)

    @model_validator(mode="after")
    def validate_total(self) -> "PassengerCountsRequest":
        if self.adults + self.children + self.infants <= 0:
            raise ValueError("At least one passenger is required")
        return self


class SearchPreferencesRequest(BaseModel):
    sort: SearchSortOption = SearchSortOption.best
    max_transfers: int | None = Field(default=None, ge=0)
    max_price: Decimal | None = Field(default=None, ge=0)
    max_duration_minutes: int | None = Field(default=None, ge=0)


class SearchCreateRequest(BaseModel):
    origin: SearchLocationReference
    destination: SearchLocationReference
    date: date
    passengers: PassengerCountsRequest = Field(
        default_factory=PassengerCountsRequest,
    )
    transport_types: list[TransportType] = Field(default_factory=list)
    preferences: SearchPreferencesRequest = Field(
        default_factory=SearchPreferencesRequest,
    )


class SearchCreateResponse(BaseModel):
    search_id: UUID
    status: SearchStatus
    results_url: str
    poll_after_ms: int
    expires_at: datetime


class TransportTypeFacetResponse(BaseModel):
    value: TransportType
    count: int


class TransferFacetResponse(BaseModel):
    value: int
    count: int


class PriceRangeResponse(BaseModel):
    min: float | None
    max: float | None


class DurationRangeResponse(BaseModel):
    min: int | None
    max: int | None


class SearchResultsMetaResponse(BaseModel):
    total_found: int
    currency: str
    stale_after_sec: int


class SearchResultsFacetsResponse(BaseModel):
    transport_types: list[TransportTypeFacetResponse]
    transfers: list[TransferFacetResponse]
    price: PriceRangeResponse
    duration_minutes: DurationRangeResponse


class SearchResultsResponse(BaseModel):
    search_id: UUID
    status: SearchStatus
    is_complete: bool
    last_update: int
    meta: SearchResultsMetaResponse
    facets: SearchResultsFacetsResponse
    items: list[RouteListItemResponse]
    error_message: str | None = None
