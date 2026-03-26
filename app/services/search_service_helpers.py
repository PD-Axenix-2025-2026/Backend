from __future__ import annotations

from datetime import datetime, timedelta
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit
from uuid import UUID

from app.core.config import Settings
from app.services.contracts import SearchStatus
from app.services.search_results import SearchHandle
from app.services.search_store_models import utc_now


def build_search_expiration(settings: Settings) -> datetime:
    return utc_now() + timedelta(seconds=settings.search_ttl_seconds)


def build_search_handle(
    settings: Settings,
    *,
    search_id: UUID,
    expires_at: datetime,
) -> SearchHandle:
    return SearchHandle(
        search_id=search_id,
        status=SearchStatus.pending,
        results_url=f"{settings.api_prefix}/searches/{search_id}/results",
        poll_after_ms=settings.search_poll_after_ms,
        expires_at=expires_at,
    )


def build_checkout_expiration(
    settings: Settings,
    *,
    search_expires_at: datetime,
) -> datetime:
    return min(
        search_expires_at,
        utc_now() + timedelta(seconds=settings.checkout_link_ttl_seconds),
    )


def build_checkout_url(
    base_url: str,
    *,
    route_id: UUID,
    search_id: UUID,
    provider_offer_id: str | None,
) -> str:
    parsed_url = urlsplit(base_url)
    query_params = dict(parse_qsl(parsed_url.query, keep_blank_values=True))
    query_params["route_id"] = str(route_id)
    query_params["search_id"] = str(search_id)
    if provider_offer_id:
        query_params["provider_offer_id"] = provider_offer_id

    return urlunsplit(
        (
            parsed_url.scheme,
            parsed_url.netloc,
            parsed_url.path,
            urlencode(query_params),
            parsed_url.fragment,
        )
    )


__all__ = [
    "build_checkout_expiration",
    "build_checkout_url",
    "build_search_expiration",
    "build_search_handle",
]
