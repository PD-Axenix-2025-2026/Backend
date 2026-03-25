from collections.abc import AsyncIterator
from typing import Annotated, cast

from fastapi import Depends, Request
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.container import AppContainer
from app.services.location_service import LocationService
from app.services.route_aggregation import RouteAggregationService
from app.services.search_service import SearchService


def get_container(request: Request) -> AppContainer:
    return cast(AppContainer, request.app.state.container)


async def get_db_session(
    container: Annotated[AppContainer, Depends(get_container)],
) -> AsyncIterator[AsyncSession]:
    async with container.session_factory() as session:
        yield session


def get_route_aggregation_service(
    session: Annotated[AsyncSession, Depends(get_db_session)],
    container: Annotated[AppContainer, Depends(get_container)],
) -> RouteAggregationService:
    return container.build_route_aggregation_service(session)


def get_location_service(
    session: Annotated[AsyncSession, Depends(get_db_session)],
    container: Annotated[AppContainer, Depends(get_container)],
) -> LocationService:
    return container.build_location_service(session)


def get_search_service(
    container: Annotated[AppContainer, Depends(get_container)],
) -> SearchService:
    return container.search_service
