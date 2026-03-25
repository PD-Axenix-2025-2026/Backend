from sqlalchemy import Select, func, or_, select

from app.models.location import Location
from app.models.route_segment import RouteSegment
from app.repositories.base import BaseRepository
from app.services.contracts import RouteCandidate, RouteSearchCriteria


class RouteSegmentRepository(BaseRepository):
    async def find_direct_candidates(
        self,
        criteria: RouteSearchCriteria,
    ) -> list[RouteCandidate]:
        origin = select(Location.id).where(Location.code == criteria.origin_code)
        destination = select(Location.id).where(
            Location.code == criteria.destination_code
        )

        statement: Select[tuple[RouteSegment]] = (
            select(RouteSegment)
            .where(RouteSegment.origin_location_id.in_(origin))
            .where(RouteSegment.destination_location_id.in_(destination))
            .where(func.date(RouteSegment.departure_at) == criteria.travel_date)
            .where(RouteSegment.is_active.is_(True))
            .where(RouteSegment.valid_from <= func.now())
            .where(
                or_(
                    RouteSegment.valid_to.is_(None),
                    RouteSegment.valid_to >= func.now(),
                )
            )
            .order_by(RouteSegment.departure_at.asc())
        )
        result = await self.session.execute(statement)
        segments = result.scalars().all()

        return [
            RouteCandidate(
                source="database",
                segment_ids=(segment.id,),
                total_price=segment.price_amount,
                total_duration_minutes=segment.duration_minutes,
                transfers=0,
            )
            for segment in segments
        ]
