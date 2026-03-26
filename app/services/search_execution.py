from __future__ import annotations

import logging
from collections.abc import Callable, Sequence
from typing import Protocol
from uuid import UUID

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.core.logging import build_log_extra
from app.models.route_segment import RouteSegment
from app.services.contracts import RouteCandidate, RouteSearchCriteria
from app.services.route_aggregation import RouteAggregationService
from app.services.search_snapshot_builder import (
    build_route_snapshot,
    resolve_candidate_segments,
)
from app.services.search_store_models import RouteSnapshot

logger = logging.getLogger("app.services.search_service")


class RouteSegmentRepositoryProtocol(Protocol):
    async def list_by_ids(self, segment_ids: Sequence[UUID]) -> list[RouteSegment]: ...


RouteSegmentRepositoryFactory = Callable[[AsyncSession], RouteSegmentRepositoryProtocol]
RouteAggregationFactory = Callable[[AsyncSession], RouteAggregationService]


class RouteSnapshotLoader:
    def __init__(
        self,
        session_factory: async_sessionmaker[AsyncSession],
        route_segment_repository_factory: RouteSegmentRepositoryFactory,
        route_aggregation_factory: RouteAggregationFactory,
    ) -> None:
        self._session_factory = session_factory
        self._route_segment_repository_factory = route_segment_repository_factory
        self._route_aggregation_factory = route_aggregation_factory

    async def load_route_snapshots(
        self,
        *,
        search_id: UUID,
        criteria: RouteSearchCriteria,
    ) -> list[RouteSnapshot]:
        logger.debug(
            "Loading route snapshots from providers",
            extra=build_log_extra(search_id=search_id),
        )
        async with self._session_factory() as session:
            candidates = await self._search_candidates(
                session=session,
                criteria=criteria,
                search_id=search_id,
            )
            segments_by_id = await self._load_segments_by_id(
                session=session,
                candidates=candidates,
                search_id=search_id,
            )

        routes = self._build_route_snapshots(
            search_id=search_id,
            candidates=candidates,
            segments_by_id=segments_by_id,
        )
        logger.debug(
            "Route snapshots built candidate_count=%s route_count=%s segment_count=%s",
            len(candidates),
            len(routes),
            len(segments_by_id),
            extra=build_log_extra(search_id=search_id),
        )
        return routes

    async def _search_candidates(
        self,
        *,
        session: AsyncSession,
        criteria: RouteSearchCriteria,
        search_id: UUID,
    ) -> list[RouteCandidate]:
        route_service = self._route_aggregation_factory(session)
        candidates = await route_service.search(criteria)
        logger.debug(
            "Search candidates loaded candidate_count=%s",
            len(candidates),
            extra=build_log_extra(search_id=search_id),
        )
        return candidates

    async def _load_segments_by_id(
        self,
        *,
        session: AsyncSession,
        candidates: Sequence[RouteCandidate],
        search_id: UUID,
    ) -> dict[UUID, RouteSegment]:
        segment_ids = _collect_segment_ids(candidates)
        logger.debug(
            "Loading route segments for candidates segment_count=%s",
            len(segment_ids),
            extra=build_log_extra(search_id=search_id),
        )
        repository = self._route_segment_repository_factory(session)
        segments = await repository.list_by_ids(segment_ids)
        return {segment.id: segment for segment in segments}

    def _build_route_snapshots(
        self,
        *,
        search_id: UUID,
        candidates: Sequence[RouteCandidate],
        segments_by_id: dict[UUID, RouteSegment],
    ) -> list[RouteSnapshot]:
        routes: list[RouteSnapshot] = []
        for candidate in candidates:
            segments = resolve_candidate_segments(
                candidate,
                segments_by_id=segments_by_id,
            )
            if segments is None:
                continue
            routes.append(
                build_route_snapshot(
                    search_id=search_id,
                    candidate=candidate,
                    segments=segments,
                )
            )
        return routes


def _collect_segment_ids(candidates: Sequence[RouteCandidate]) -> tuple[UUID, ...]:
    return tuple(
        dict.fromkeys(
            segment_id
            for candidate in candidates
            for segment_id in candidate.segment_ids
        )
    )


__all__ = [
    "RouteAggregationFactory",
    "RouteSegmentRepositoryFactory",
    "RouteSegmentRepositoryProtocol",
    "RouteSnapshotLoader",
]
