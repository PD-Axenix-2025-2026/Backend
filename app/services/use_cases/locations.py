import logging

from app.models.enums import LocationType
from app.models.location import Location
from app.services.ports import LocationReadPort

logger = logging.getLogger("app.services.location_service")


class ListLocationsUseCase:
    def __init__(self, location_reader: LocationReadPort) -> None:
        self._location_reader = location_reader

    async def execute(
        self,
        prefix: str,
        limit: int = 10,
        location_types: tuple[LocationType, ...] = (),
    ) -> list[Location]:
        logger.debug(
            "Listing locations by prefix prefix=%s limit=%s location_types=%s",
            prefix,
            limit,
            [location_type.value for location_type in location_types] or ["all"],
        )
        locations = await self._location_reader.list_by_prefix(
            prefix=prefix,
            limit=limit,
            location_types=location_types,
        )
        logger.debug(
            "Locations listed result_count=%s prefix=%s",
            len(locations),
            prefix,
        )
        return locations
