from __future__ import annotations

import asyncio
from datetime import datetime
from uuid import UUID

from app.services.contracts import RouteSearchCriteria, SearchStatus
from app.services.search_store_models import (
    MoneySnapshot,
    RouteNotFoundError,
    RouteSegmentSnapshot,
    RouteSnapshot,
    SearchNotFoundError,
    SearchRecord,
    utc_now,
)


class InMemorySearchStore:
    def __init__(self) -> None:
        self._lock = asyncio.Lock()
        self._searches: dict[UUID, SearchRecord] = {}
        self._route_index: dict[UUID, UUID] = {}

    async def create_search(
        self,
        search_id: UUID,
        criteria: RouteSearchCriteria,
        expires_at: datetime,
    ) -> SearchRecord:
        async with self._lock:
            self._cleanup_expired_locked()
            record = self._build_pending_record(
                search_id=search_id,
                criteria=criteria,
                expires_at=expires_at,
            )
            self._searches[search_id] = record
            return record

    async def mark_complete(
        self,
        search_id: UUID,
        routes: list[RouteSnapshot],
    ) -> SearchRecord:
        async with self._lock:
            self._cleanup_expired_locked()
            record = self._get_active_search_locked(search_id)
            self._unindex_routes_locked(record.routes)
            record.mark_complete(routes=tuple(routes), updated_at=utc_now())
            self._index_routes_locked(search_id=search_id, routes=record.routes)
            return record

    async def mark_failed(
        self,
        search_id: UUID,
        error_message: str,
    ) -> SearchRecord:
        async with self._lock:
            self._cleanup_expired_locked()
            record = self._get_active_search_locked(search_id)
            record.mark_failed(error_message=error_message, updated_at=utc_now())
            return record

    async def get_search(self, search_id: UUID) -> SearchRecord:
        async with self._lock:
            self._cleanup_expired_locked()
            return self._get_active_search_locked(search_id)

    async def get_route(self, route_id: UUID) -> tuple[SearchRecord, RouteSnapshot]:
        async with self._lock:
            self._cleanup_expired_locked()
            search_id = self._route_index.get(route_id)
            if search_id is None:
                raise RouteNotFoundError(route_id)

            record = self._get_active_search_locked(search_id)
            return record, self._find_route_locked(record, route_id)

    def _build_pending_record(
        self,
        *,
        search_id: UUID,
        criteria: RouteSearchCriteria,
        expires_at: datetime,
    ) -> SearchRecord:
        now = utc_now()
        return SearchRecord(
            search_id=search_id,
            criteria=criteria,
            created_at=now,
            updated_at=now,
            expires_at=expires_at,
            status=SearchStatus.pending,
            last_update=0,
        )

    def _cleanup_expired_locked(self) -> None:
        expired_search_ids = [
            search_id
            for search_id, record in self._searches.items()
            if self._is_expired(record)
        ]
        for search_id in expired_search_ids:
            self._remove_search_locked(search_id)

    def _get_active_search_locked(self, search_id: UUID) -> SearchRecord:
        record = self._searches.get(search_id)
        if record is None:
            raise SearchNotFoundError(search_id)
        if self._is_expired(record):
            self._remove_search_locked(search_id)
            raise SearchNotFoundError(search_id)
        return record

    def _find_route_locked(
        self,
        record: SearchRecord,
        route_id: UUID,
    ) -> RouteSnapshot:
        for route in record.routes:
            if route.route_id == route_id:
                return route
        raise RouteNotFoundError(route_id)

    def _index_routes_locked(
        self,
        *,
        search_id: UUID,
        routes: tuple[RouteSnapshot, ...],
    ) -> None:
        for route in routes:
            self._route_index[route.route_id] = search_id

    def _unindex_routes_locked(self, routes: tuple[RouteSnapshot, ...]) -> None:
        for route in routes:
            self._route_index.pop(route.route_id, None)

    def _remove_search_locked(self, search_id: UUID) -> None:
        record = self._searches.pop(search_id, None)
        if record is None:
            return
        self._unindex_routes_locked(record.routes)

    def _is_expired(self, record: SearchRecord) -> bool:
        return record.expires_at <= utc_now()


__all__ = [
    "InMemorySearchStore",
    "MoneySnapshot",
    "RouteNotFoundError",
    "RouteSegmentSnapshot",
    "RouteSnapshot",
    "SearchNotFoundError",
    "SearchRecord",
    "utc_now",
]
