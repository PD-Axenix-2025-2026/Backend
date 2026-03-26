from typing import Annotated, cast

from fastapi import Depends, Request

from app.core.container import AppContainer
from app.services.use_cases import (
    CreateCheckoutLinkUseCase,
    CreateSearchUseCase,
    GetRouteDetailUseCase,
    GetSearchResultsUseCase,
    ListLocationsUseCase,
)


def get_container(request: Request) -> AppContainer:
    return cast(AppContainer, request.app.state.container)


def get_list_locations_use_case(
    container: Annotated[AppContainer, Depends(get_container)],
) -> ListLocationsUseCase:
    return container.list_locations_use_case


def get_create_search_use_case(
    container: Annotated[AppContainer, Depends(get_container)],
) -> CreateSearchUseCase:
    return container.create_search_use_case


def get_search_results_use_case(
    container: Annotated[AppContainer, Depends(get_container)],
) -> GetSearchResultsUseCase:
    return container.get_search_results_use_case


def get_route_detail_use_case(
    container: Annotated[AppContainer, Depends(get_container)],
) -> GetRouteDetailUseCase:
    return container.get_route_detail_use_case


def get_create_checkout_link_use_case(
    container: Annotated[AppContainer, Depends(get_container)],
) -> CreateCheckoutLinkUseCase:
    return container.create_checkout_link_use_case
