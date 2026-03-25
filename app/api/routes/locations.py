from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Query

from app.api.dependencies import get_location_service
from app.api.query_parsers import parse_csv_enum_values
from app.models.enums import LocationType
from app.schemas.locations import (
    LocationAutocompleteItem,
    LocationAutocompleteResponse,
)
from app.services.location_service import LocationService

router = APIRouter()


@router.get("/locations", response_model=LocationAutocompleteResponse)
async def list_locations(
    prefix: Annotated[str, Query(min_length=2)],
    service: Annotated[LocationService, Depends(get_location_service)],
    types: str | None = None,
    limit: Annotated[int, Query(ge=1, le=20)] = 10,
) -> LocationAutocompleteResponse:
    normalized_prefix = prefix.strip()
    if len(normalized_prefix) < 2:
        raise HTTPException(
            status_code=422,
            detail="Query parameter 'prefix' is too short",
        )

    location_types = parse_csv_enum_values(
        types,
        enum_type=LocationType,
        parameter_name="types",
    )
    locations = await service.list_by_prefix(
        prefix=normalized_prefix,
        limit=limit,
        location_types=location_types,
    )
    return LocationAutocompleteResponse(
        items=[
            LocationAutocompleteItem(
                id=location.id,
                type=location.location_type,
                label=location.name,
                city_label=location.city_name,
                code=location.code,
                country_code=location.country_code,
            )
            for location in locations
        ]
    )
