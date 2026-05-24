import logging

from app.models.enums import LocationType
from app.models.location import Location
from app.services.application.ports import LocationReadPort

logger = logging.getLogger(__name__)


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

        # вначале ищем города (половину от нужного кол-ва)
        cities_limit = limit // 2

        cities = []
        if LocationType.city in location_types:
            cities = await self._location_reader.list_by_prefix(
                prefix=prefix,
                limit=cities_limit,
                location_types=(LocationType.city,),
            )

        # затем оставшиеся, если нужно
        remaining_limit = limit - len(cities)

        other_locations = []
        if remaining_limit > 0:
            other_locations = await self._location_reader.list_by_prefix(
                prefix=prefix,
                limit=remaining_limit,
                location_types=tuple(
                    loc_type
                    for loc_type in location_types
                    if loc_type != LocationType.city
                ),
            )

        locations = cities + other_locations

        logger.debug(
            "Locations listed result_count=%s prefix=%s",
            len(locations),
            prefix,
        )
        return self._sort_by_location_type(locations, location_types)

    def _sort_by_location_type(
        self, locations: list[Location], location_types: tuple[LocationType, ...] = ()
    ) -> list[Location]:
        sorted_locations = []

        location_types_order = [
            LocationType.city,
            LocationType.airport,
            LocationType.railway_station,
            LocationType.bus_station,
        ]

        def add_location_type_entries(location_type: LocationType) -> None:
            for location in locations:
                if (
                    not location_types
                    or location_type in location_types
                    and location.location_type == location_type
                ):
                    sorted_locations.append(location)

        for loc_type in location_types_order:
            add_location_type_entries(loc_type)

        return sorted_locations
