import logging
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Query

from app.api.dependencies import get_list_locations_use_case
from app.api.query_parsers import parse_csv_enum_values
from app.models.enums import LocationType
from app.schemas.locations import (
    LocationAutocompleteItem,
    LocationAutocompleteResponse,
)
from app.services.use_cases import ListLocationsUseCase

router = APIRouter()
logger = logging.getLogger(__name__)


@router.get("/locations", response_model=LocationAutocompleteResponse)
async def list_locations(
    prefix: Annotated[str, Query(min_length=2)],
    use_case: Annotated[ListLocationsUseCase, Depends(get_list_locations_use_case)],
    types: str | None = None,
    limit: Annotated[int, Query(ge=1, le=20)] = 10,
) -> LocationAutocompleteResponse:
    normalized_prefix = prefix.strip()
    logger.debug(
        (
            "Location autocomplete requested "
            "prefix=%s prefix_length=%s raw_types=%s limit=%s"
        ),
        normalized_prefix,
        len(normalized_prefix),
        types or "-",
        limit,
    )
    if len(normalized_prefix) < 2:
        logger.warning(
            "Location autocomplete rejected because prefix is too short "
            "prefix_length=%s",
            len(normalized_prefix),
        )
        raise HTTPException(
            status_code=422,
            detail="Query parameter 'prefix' is too short",
        )

    location_types = parse_csv_enum_values(
        types,
        enum_type=LocationType,
        parameter_name="types",
    )
    locations = await use_case.execute(
        prefix=normalized_prefix,
        limit=limit,
        location_types=location_types,
    )
    logger.debug(
        "Location autocomplete completed result_count=%s location_types=%s",
        len(locations),
        [location_type.value for location_type in location_types] or ["all"],
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
