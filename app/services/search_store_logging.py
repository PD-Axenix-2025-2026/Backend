from __future__ import annotations

import logging
from datetime import datetime
from uuid import UUID

from app.core.logging import build_log_extra
from app.services.search_store_models import SearchRecord

logger = logging.getLogger("app.services.search_store")


def log_expired_search_cleanup(expired_count: int) -> None:
    if expired_count:
        logger.debug("Cleaning up expired searches count=%s", expired_count)


def log_search_created(*, search_id: UUID, expires_at: datetime) -> None:
    logger.info(
        "Search record created expires_at=%s",
        expires_at.isoformat(),
        extra=_search_log_extra(search_id),
    )


def log_search_completed(*, search_id: UUID, record: SearchRecord) -> None:
    logger.info(
        "Search record marked complete route_count=%s last_update=%s",
        len(record.routes),
        record.last_update,
        extra=_search_log_extra(search_id),
    )


def log_search_failed(*, search_id: UUID, error_message: str) -> None:
    logger.error(
        "Search record marked failed error_message=%s",
        error_message,
        extra=_search_log_extra(search_id),
    )


def log_search_requested(search_id: UUID) -> None:
    logger.debug("Search record requested", extra=_search_log_extra(search_id))


def log_missing_search(search_id: UUID) -> None:
    logger.warning(
        "Search lookup failed because record was not found",
        extra=_search_log_extra(search_id),
    )


def log_expired_search(search_id: UUID) -> None:
    logger.warning(
        "Search lookup failed because record expired",
        extra=_search_log_extra(search_id),
    )


def log_route_requested(*, search_id: UUID, route_id: UUID) -> None:
    logger.debug(
        "Route snapshot requested",
        extra=_route_log_extra(search_id=search_id, route_id=route_id),
    )


def log_missing_route(
    *,
    route_id: UUID,
    search_id: UUID | None = None,
) -> None:
    logger.warning(
        "Route lookup failed because route was not found",
        extra=_route_log_extra(search_id=search_id, route_id=route_id),
    )


def log_removed_search(search_id: UUID) -> None:
    logger.debug(
        "Search record removed from in-memory store",
        extra=_search_log_extra(search_id),
    )


def _search_log_extra(search_id: UUID) -> dict[str, object]:
    return build_log_extra(search_id=search_id)


def _route_log_extra(
    *,
    search_id: UUID | None = None,
    route_id: UUID,
) -> dict[str, object]:
    return build_log_extra(search_id=search_id, route_id=route_id)


__all__ = [
    "log_expired_search",
    "log_expired_search_cleanup",
    "log_missing_route",
    "log_missing_search",
    "log_removed_search",
    "log_route_requested",
    "log_search_completed",
    "log_search_created",
    "log_search_failed",
    "log_search_requested",
]
