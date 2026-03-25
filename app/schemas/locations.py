from uuid import UUID

from pydantic import BaseModel

from app.models.enums import LocationType


class LocationAutocompleteItem(BaseModel):
    id: UUID
    type: LocationType
    label: str
    city_label: str | None = None
    code: str | None = None
    country_code: str | None = None


class LocationAutocompleteResponse(BaseModel):
    items: list[LocationAutocompleteItem]
