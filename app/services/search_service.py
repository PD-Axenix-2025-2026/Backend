from __future__ import annotations

from uuid import UUID

from app.services.models import (
    CheckoutLinkInfo,
    RouteSearchCriteria,
    RouteSnapshot,
    SearchHandle,
    SearchResultsPage,
    SearchResultsQuery,
)
from app.services.runtime import SearchRuntimeCoordinator
from app.services.use_cases import (
    CreateCheckoutLinkUseCase,
    CreateSearchUseCase,
    GetRouteDetailUseCase,
    GetSearchResultsUseCase,
)


class SearchService:
    def __init__(
        self,
        create_search_use_case: CreateSearchUseCase,
        get_search_results_use_case: GetSearchResultsUseCase,
        get_route_detail_use_case: GetRouteDetailUseCase,
        create_checkout_link_use_case: CreateCheckoutLinkUseCase,
        runtime_coordinator: SearchRuntimeCoordinator,
    ) -> None:
        self._create_search_use_case = create_search_use_case
        self._get_search_results_use_case = get_search_results_use_case
        self._get_route_detail_use_case = get_route_detail_use_case
        self._create_checkout_link_use_case = create_checkout_link_use_case
        self._runtime_coordinator = runtime_coordinator

    async def create_search(self, criteria: RouteSearchCriteria) -> SearchHandle:
        return await self._create_search_use_case.execute(criteria)

    async def get_results(
        self,
        search_id: UUID,
        query: SearchResultsQuery,
    ) -> SearchResultsPage:
        return await self._get_search_results_use_case.execute(search_id, query)

    async def get_route_detail(self, route_id: UUID) -> RouteSnapshot:
        return await self._get_route_detail_use_case.execute(route_id)

    async def build_checkout_link(
        self,
        route_id: UUID,
        provider_offer_id: str | None = None,
    ) -> CheckoutLinkInfo:
        return await self._create_checkout_link_use_case.execute(
            route_id=route_id,
            provider_offer_id=provider_offer_id,
        )

    async def shutdown(self) -> None:
        await self._runtime_coordinator.shutdown()


__all__ = ["SearchService"]
