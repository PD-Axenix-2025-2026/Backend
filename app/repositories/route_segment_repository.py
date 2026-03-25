from collections.abc import Sequence
from uuid import UUID

from sqlalchemy import Select, func, or_, select

from app.models.route_segment import RouteSegment
from app.repositories.base import BaseRepository
from app.services.contracts import RouteCandidate, RouteSearchCriteria


class RouteSegmentRepository(BaseRepository):
    async def find_direct_candidates(
        self,
        criteria: RouteSearchCriteria,
    ) -> list[RouteCandidate]:
        statement: Select[tuple[RouteSegment]] = (
            select(RouteSegment)
            .where(RouteSegment.origin_location_id == criteria.origin_id)
            .where(RouteSegment.destination_location_id == criteria.destination_id)
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
        if criteria.transport_types:
            statement = statement.where(
                RouteSegment.transport_type.in_(criteria.transport_types)
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

    async def list_by_ids(self, segment_ids: Sequence[UUID]) -> list[RouteSegment]:
        if not segment_ids:
            return []

        statement: Select[tuple[RouteSegment]] = select(RouteSegment).where(
            RouteSegment.id.in_(segment_ids)
        )
        result = await self.session.execute(statement)
        return list(result.scalars().all())
