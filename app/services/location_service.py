from app.models.location import Location
from app.repositories.location_repository import LocationRepository


class LocationService:
    def __init__(self, location_repository: LocationRepository) -> None:
        self._location_repository = location_repository

    async def list_by_prefix(self, prefix: str, limit: int = 10) -> list[Location]:
        return await self._location_repository.list_by_prefix(prefix=prefix, limit=limit)
