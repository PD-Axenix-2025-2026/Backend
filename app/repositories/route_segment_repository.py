import logging
from collections.abc import Sequence
from uuid import UUID

from sqlalchemy import Select, func, or_, select

from app.models.route_segment import RouteSegment
from app.repositories.base import BaseRepository
from app.services.contracts import RouteCandidate, RouteSearchCriteria

logger = logging.getLogger(__name__)


class RouteSegmentRepository(BaseRepository):
    async def find_direct_candidates(
        self,
        criteria: RouteSearchCriteria,
    ) -> list[RouteCandidate]:
        logger.debug(
            (
                "Finding direct route candidates "
                "origin_id=%s destination_id=%s travel_date=%s "
                "transport_types=%s"
            ),
            criteria.origin_id,
            criteria.destination_id,
            criteria.travel_date,
            self._serialize_transport_types(criteria),
        )
        segments = await self._load_segments(
            statement=self._build_direct_candidates_statement(criteria),
            error_message=(
                "Failed to find direct route candidates "
                "origin_id=%s destination_id=%s travel_date=%s"
            ),
            error_args=(
                criteria.origin_id,
                criteria.destination_id,
                criteria.travel_date,
            ),
        )
        candidates = self._build_direct_candidates(segments)
        logger.debug(
            "Direct route candidates found candidate_count=%s",
            len(candidates),
        )
        return candidates

    async def list_by_ids(self, segment_ids: Sequence[UUID]) -> list[RouteSegment]:
        if not segment_ids:
            logger.debug("Route segment lookup skipped because segment_ids are empty")
            return []

        logger.debug("Listing route segments by ids count=%s", len(segment_ids))
        return await self._load_segments(
            statement=select(RouteSegment).where(RouteSegment.id.in_(segment_ids)),
            error_message="Failed to list route segments by ids count=%s",
            error_args=(len(segment_ids),),
            success_message="Route segments loaded result_count=%s",
        )

    def _build_direct_candidates_statement(
        self,
        criteria: RouteSearchCriteria,
    ) -> Select[tuple[RouteSegment]]:
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
            return statement.where(
                RouteSegment.transport_type.in_(criteria.transport_types)
            )
        return statement

    def _build_direct_candidates(
        self,
        segments: Sequence[RouteSegment],
    ) -> list[RouteCandidate]:
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

    async def _load_segments(
        self,
        *,
        statement: Select[tuple[RouteSegment]],
        error_message: str,
        error_args: tuple[object, ...],
        success_message: str | None = None,
    ) -> list[RouteSegment]:
        try:
            result = await self.session.execute(statement)
        except Exception:
            logger.exception(error_message, *error_args)
            raise

        segments = list(result.scalars().all())
        if success_message is not None:
            logger.debug(success_message, len(segments))
        return segments

    def _serialize_transport_types(
        self,
        criteria: RouteSearchCriteria,
    ) -> list[str]:
        values = [transport_type.value for transport_type in criteria.transport_types]
        return values or ["all"]
