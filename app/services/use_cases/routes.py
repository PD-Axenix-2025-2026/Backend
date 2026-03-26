from uuid import UUID

from app.core.config import Settings
from app.services.models import CheckoutLinkInfo, RouteSnapshot
from app.services.ports import SearchStateStorePort
from app.services.search_service_helpers import (
    build_checkout_expiration,
    build_checkout_url,
)
from app.services.search_service_logging import (
    log_checkout_generated,
    log_route_detail_completed,
    log_route_detail_requested,
)


class GetRouteDetailUseCase:
    def __init__(self, search_state_store: SearchStateStorePort) -> None:
        self._search_state_store = search_state_store

    async def execute(self, route_id: UUID) -> RouteSnapshot:
        log_route_detail_requested(route_id)
        _record, route = await self._search_state_store.get_route(route_id)
        log_route_detail_completed(search_id=route.search_id, route_id=route_id)
        return route


class CreateCheckoutLinkUseCase:
    def __init__(
        self,
        settings: Settings,
        search_state_store: SearchStateStorePort,
    ) -> None:
        self._settings = settings
        self._search_state_store = search_state_store

    async def execute(
        self,
        route_id: UUID,
        provider_offer_id: str | None = None,
    ) -> CheckoutLinkInfo:
        record, route = await self._search_state_store.get_route(route_id)
        log_checkout_generated(
            search_id=record.search_id,
            route_id=route_id,
            provider_offer_id=provider_offer_id,
        )
        return CheckoutLinkInfo(
            method="GET",
            url=build_checkout_url(
                self._settings.mock_checkout_base_url,
                route_id=route.route_id,
                search_id=record.search_id,
                provider_offer_id=provider_offer_id,
            ),
            expires_at=build_checkout_expiration(
                self._settings,
                search_expires_at=record.expires_at,
            ),
        )


__all__ = [
    "CreateCheckoutLinkUseCase",
    "GetRouteDetailUseCase",
]
