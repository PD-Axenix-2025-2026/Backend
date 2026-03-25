from app.models.enums import LocationType
from app.models.location import Location
from app.repositories.location_repository import LocationRepository


class LocationService:
    def __init__(self, location_repository: LocationRepository) -> None:
        self._location_repository = location_repository

    async def list_by_prefix(
        self,
        prefix: str,
        limit: int = 10,
        location_types: tuple[LocationType, ...] = (),
    ) -> list[Location]:
        return await self._location_repository.list_by_prefix(
            prefix=prefix,
            limit=limit,
            location_types=location_types,
        )
