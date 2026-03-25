from uuid import UUID

from sqlalchemy import Select, or_, select

from app.models.enums import LocationType
from app.models.location import Location
from app.repositories.base import BaseRepository


class LocationRepository(BaseRepository):
    async def get_by_id(self, location_id: UUID) -> Location | None:
        statement = select(Location).where(Location.id == location_id)
        result = await self.session.execute(statement)
        return result.scalar_one_or_none()

    async def list_by_prefix(
        self,
        prefix: str,
        limit: int = 10,
        location_types: tuple[LocationType, ...] = (),
    ) -> list[Location]:
        statement: Select[tuple[Location]] = (
            select(Location)
            .where(
                or_(
                    Location.name.ilike(f"{prefix}%"),
                    Location.city_name.ilike(f"{prefix}%"),
                    Location.code.ilike(f"{prefix}%"),
                )
            )
            .order_by(Location.name.asc())
            .limit(limit)
        )
        if location_types:
            statement = statement.where(Location.location_type.in_(location_types))

        result = await self.session.execute(statement)
        return list(result.scalars().all())
