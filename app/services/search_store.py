from __future__ import annotations

import asyncio
from datetime import datetime
from uuid import UUID

from app.services.contracts import RouteSearchCriteria
from app.services.search_store_logging import (
    log_expired_search,
    log_expired_search_cleanup,
    log_missing_route,
    log_missing_search,
    log_route_requested,
    log_search_completed,
    log_search_created,
    log_search_failed,
    log_search_requested,
)
from app.services.search_store_models import (
    RouteNotFoundError,
    RouteSnapshot,
    SearchNotFoundError,
    SearchRecord,
    utc_now,
)
from app.services.search_store_ops import (
    cleanup_expired_searches,
    create_pending_record,
    index_routes,
    require_active_search,
    require_indexed_search_id,
    require_route,
    unindex_routes,
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
            record = create_pending_record(
                search_id=search_id,
                criteria=criteria,
                expires_at=expires_at,
            )
            self._searches[search_id] = record
            log_search_created(search_id=search_id, expires_at=expires_at)
            return record

    async def mark_complete(
        self,
        search_id: UUID,
        routes: list[RouteSnapshot],
    ) -> SearchRecord:
        async with self._lock:
            self._cleanup_expired_locked()
            record = self._require_active_search_locked(search_id)
            unindex_routes(self._route_index, record.routes)
            record.mark_complete(routes=tuple(routes), updated_at=utc_now())
            index_routes(
                self._route_index,
                search_id=search_id,
                routes=record.routes,
            )
            log_search_completed(search_id=search_id, record=record)
            return record

    async def mark_failed(
        self,
        search_id: UUID,
        error_message: str,
    ) -> SearchRecord:
        async with self._lock:
            self._cleanup_expired_locked()
            record = self._require_active_search_locked(search_id)
            record.mark_failed(error_message=error_message, updated_at=utc_now())
            log_search_failed(search_id=search_id, error_message=error_message)
            return record

    async def get_search(self, search_id: UUID) -> SearchRecord:
        async with self._lock:
            self._cleanup_expired_locked()
            log_search_requested(search_id)
            return self._require_active_search_locked(search_id)

    async def get_route(self, route_id: UUID) -> tuple[SearchRecord, RouteSnapshot]:
        async with self._lock:
            self._cleanup_expired_locked()
            search_id = self._require_indexed_search_id_locked(route_id)
            record = self._require_active_search_locked(search_id)
            route = self._require_route_locked(record, route_id)
            log_route_requested(search_id=search_id, route_id=route_id)
            return record, route

    def _cleanup_expired_locked(self) -> None:
        expired_search_ids = cleanup_expired_searches(self._searches, self._route_index)
        log_expired_search_cleanup(len(expired_search_ids))

    def _require_active_search_locked(self, search_id: UUID) -> SearchRecord:
        was_present = search_id in self._searches
        try:
            return require_active_search(
                self._searches,
                self._route_index,
                search_id=search_id,
            )
        except SearchNotFoundError:
            if was_present:
                log_expired_search(search_id)
            else:
                log_missing_search(search_id)
            raise

    def _require_indexed_search_id_locked(self, route_id: UUID) -> UUID:
        try:
            return require_indexed_search_id(self._route_index, route_id=route_id)
        except RouteNotFoundError:
            log_missing_route(route_id=route_id)
            raise

    def _require_route_locked(
        self,
        record: SearchRecord,
        route_id: UUID,
    ) -> RouteSnapshot:
        try:
            return require_route(record, route_id=route_id)
        except RouteNotFoundError:
            log_missing_route(route_id=route_id, search_id=record.search_id)
            raise


__all__ = ["InMemorySearchStore"]
