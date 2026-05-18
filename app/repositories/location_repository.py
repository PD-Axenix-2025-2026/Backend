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
                    Location.code.ilike(f"{prefix}%"),
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

    async def list_by_rzd_codes(self, codes: tuple[str, ...]) -> dict[str, Location]:
        return await self._list_by_field_codes(field_name="rzd_code", codes=codes)

    async def list_by_yandex_codes(
        self,
        codes: tuple[str, ...],
    ) -> dict[str, Location]:
        return await self._list_by_field_codes(field_name="yandex_code", codes=codes)

    async def _list_by_field_codes(
        self,
        *,
        field_name: str,
        codes: tuple[str, ...],
    ) -> dict[str, Location]:
        if not codes:
            return {}

        field = getattr(Location, field_name)
        statement = select(Location).where(field.in_(codes))
        try:
            result = await self.session.execute(statement)
        except Exception:
            logger.exception(
                "Failed to list locations by provider codes field=%s code_count=%s",
                field_name,
                len(codes),
            )
            raise

        return {
            code: location
            for location in result.scalars().all()
            if (code := getattr(location, field_name)) is not None
        }
