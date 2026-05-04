import logging
from uuid import UUID

from sqlalchemy import Select, or_, select

from app.models.enums import LocationType
from app.models.location import Location
from app.repositories.base import BaseRepository

logger = logging.getLogger(__name__)


class LocationRepository(BaseRepository):
    async def get_by_id(self, location_id: UUID) -> Location | None:
        logger.debug("Fetching location by id location_id=%s", location_id)
        statement = select(Location).where(Location.id == location_id)
        try:
            result = await self.session.execute(statement)
        except Exception:
            logger.exception(
                "Failed to fetch location by id location_id=%s",
                location_id,
            )
            raise

        location = result.scalar_one_or_none()
        logger.debug(
            "Location fetch completed found=%s location_id=%s",
            location is not None,
            location_id,
        )
        return location

    async def list_by_prefix(
        self,
        prefix: str,
        limit: int = 10,
        location_types: tuple[LocationType, ...] = (),
    ) -> list[Location]:
        logger.debug(
            (
                "Listing locations by prefix in repository "
                "prefix=%s limit=%s location_types=%s"
            ),
            prefix,
            limit,
            [location_type.value for location_type in location_types] or ["all"],
        )
        statement: Select[tuple[Location]] = (
            select(Location)
            .where(
                or_(
                    Location.name.ilike(f"{prefix}%"),
                    Location.city_name.ilike(f"{prefix}%"),
                    # Location.code.ilike(f"{prefix}%"),
                )
            )
            .order_by(Location.name.asc())
            .limit(limit)
        )
        if location_types:
            statement = statement.where(Location.location_type.in_(location_types))

        try:
            result = await self.session.execute(statement)
        except Exception:
            logger.exception(
                "Failed to list locations by prefix prefix=%s limit=%s",
                prefix,
                limit,
            )
            raise

        locations = list(result.scalars().all())
        logger.debug(
            "Location repository returned result_count=%s prefix=%s",
            len(locations),
            prefix,
        )
        return locations
