from sqlalchemy import Select, select

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
        destination = select(Location.id).where(Location.code == criteria.destination_code)

        statement: Select[tuple[RouteSegment]] = (
            select(RouteSegment)
            .where(RouteSegment.origin_id.in_(origin))
            .where(RouteSegment.destination_id.in_(destination))
            .where(RouteSegment.travel_date == criteria.travel_date)
            .order_by(RouteSegment.departure_at.asc())
        )
        result = await self.session.execute(statement)
        segments = result.scalars().all()

        return [
            RouteCandidate(
                source="database",
                segment_ids=(segment.id,),
                total_price=segment.price,
                total_duration_minutes=int(
                    (segment.arrival_at - segment.departure_at).total_seconds() // 60
                ),
                transfers=0,
            )
            for segment in segments
        ]
