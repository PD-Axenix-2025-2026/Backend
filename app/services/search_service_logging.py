from __future__ import annotations

import logging
from uuid import UUID

from app.core.logging import build_log_extra
from app.services.contracts import RouteSearchCriteria, SearchResultsQuery
from app.services.search_results import SearchResultsPage

logger = logging.getLogger("app.services.search_service")


def log_search_created(*, criteria: RouteSearchCriteria, search_id: UUID) -> None:
    logger.info(
        (
            "Search created origin_id=%s destination_id=%s "
            "travel_date=%s passengers_total=%s"
        ),
        criteria.origin_id,
        criteria.destination_id,
        criteria.travel_date,
        criteria.passengers.total,
        extra=build_log_extra(search_id=search_id),
    )


def log_results_requested(*, search_id: UUID, query: SearchResultsQuery) -> None:
    logger.debug(
        (
            "Search results requested sort=%s max_price=%s "
            "max_transfers=%s max_duration_minutes=%s "
            "limit=%s offset=%s"
        ),
        query.sort.value if query.sort is not None else "-",
        query.max_price,
        query.max_transfers,
        query.max_duration_minutes,
        query.limit,
        query.offset,
        extra=build_log_extra(search_id=search_id),
    )


def log_results_prepared(*, search_id: UUID, page: SearchResultsPage) -> None:
    logger.debug(
        "Search results prepared status=%s total_found=%s item_count=%s",
        page.status.value,
        page.total_found,
        len(page.items),
        extra=build_log_extra(search_id=search_id),
    )


def log_route_detail_requested(route_id: UUID) -> None:
    logger.debug(
        "Route detail lookup requested",
        extra=build_log_extra(route_id=route_id),
    )


def log_route_detail_completed(*, search_id: UUID, route_id: UUID) -> None:
    logger.debug(
        "Route detail lookup completed search_id=%s",
        search_id,
        extra=build_log_extra(search_id=search_id, route_id=route_id),
    )


def log_checkout_generated(
    *,
    search_id: UUID,
    route_id: UUID,
    provider_offer_id: str | None,
) -> None:
    logger.info(
        "Checkout link generated provider_offer_present=%s",
        provider_offer_id is not None,
        extra=build_log_extra(search_id=search_id, route_id=route_id),
    )


def log_shutdown(task_count: int) -> None:
    logger.info("Shutting down search service task_count=%s", task_count)


def log_background_task_started(search_id: UUID) -> None:
    logger.debug(
        "Starting background search task",
        extra=build_log_extra(search_id=search_id),
    )


def log_background_search_started(
    *,
    search_id: UUID,
    criteria: RouteSearchCriteria,
) -> None:
    logger.info(
        "Background search started origin_id=%s destination_id=%s travel_date=%s",
        criteria.origin_id,
        criteria.destination_id,
        criteria.travel_date,
        extra=build_log_extra(search_id=search_id),
    )


def log_background_search_completed(*, search_id: UUID, route_count: int) -> None:
    logger.info(
        "Background search completed route_count=%s",
        route_count,
        extra=build_log_extra(search_id=search_id),
    )


def log_background_search_cancelled(search_id: UUID) -> None:
    logger.info(
        "Background search cancelled",
        extra=build_log_extra(search_id=search_id),
    )


def log_background_search_state_missing(search_id: UUID) -> None:
    logger.warning(
        "Background search stopped because state was not found",
        extra=build_log_extra(search_id=search_id),
    )


def log_background_search_failed(search_id: UUID) -> None:
    logger.exception(
        "Background search failed",
        extra=build_log_extra(search_id=search_id),
    )


__all__ = [
    "log_background_search_cancelled",
    "log_background_search_completed",
    "log_background_search_failed",
    "log_background_search_started",
    "log_background_search_state_missing",
    "log_background_task_started",
    "log_checkout_generated",
    "log_results_prepared",
    "log_results_requested",
    "log_route_detail_completed",
    "log_route_detail_requested",
    "log_search_created",
    "log_shutdown",
]
