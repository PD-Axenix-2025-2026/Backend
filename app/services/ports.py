from __future__ import annotations

from collections.abc import Sequence
from datetime import datetime
from typing import Protocol
from uuid import UUID

from app.models.enums import LocationType
from app.models.location import Location
from app.models.route_segment import RouteSegment
from app.services.models import (
    RouteCandidate,
    RouteSearchCriteria,
    RouteSnapshot,
    SearchRecord,
)


class LocationReadPort(Protocol):
    async def get_by_id(self, location_id: UUID) -> Location | None: ...

    async def list_by_prefix(
        self,
        prefix: str,
        limit: int = 10,
        location_types: tuple[LocationType, ...] = (),
    ) -> list[Location]: ...


class RouteSearchPort(Protocol):
    async def search(self, criteria: RouteSearchCriteria) -> list[RouteCandidate]: ...


class RouteSegmentReadPort(Protocol):
    async def list_by_ids(self, segment_ids: Sequence[UUID]) -> list[RouteSegment]: ...


class SearchStateStorePort(Protocol):
    async def create_search(
        self,
        search_id: UUID,
        criteria: RouteSearchCriteria,
        expires_at: datetime,
    ) -> SearchRecord: ...

    async def mark_complete(
        self,
        search_id: UUID,
        routes: list[RouteSnapshot],
    ) -> SearchRecord: ...

    async def mark_failed(
        self,
        search_id: UUID,
        error_message: str,
    ) -> SearchRecord: ...

    async def get_search(self, search_id: UUID) -> SearchRecord: ...

    async def get_route(self, route_id: UUID) -> tuple[SearchRecord, RouteSnapshot]: ...
