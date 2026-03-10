from sqlalchemy import Select, select

from app.models.location import Location
from app.repositories.base import BaseRepository


class LocationRepository(BaseRepository):
    async def list_by_prefix(self, prefix: str, limit: int = 10) -> list[Location]:
        statement: Select[tuple[Location]] = (
            select(Location)
            .where(Location.name.ilike(f"{prefix}%"))
            .order_by(Location.name.asc())
            .limit(limit)
        )
        result = await self.session.execute(statement)
        return list(result.scalars().all())
