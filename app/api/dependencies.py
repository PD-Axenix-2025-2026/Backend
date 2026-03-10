from collections.abc import AsyncIterator
from typing import Any

from fastapi import Depends, Request
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.container import AppContainer
from app.services.route_aggregation import RouteAggregationService


def get_container(request: Request) -> Any:
    return request.app.state.container


async def get_db_session(
    container: Any = Depends(get_container),
) -> AsyncIterator[AsyncSession]:
    async with container.session_factory() as session:
        yield session


def get_route_aggregation_service(
    session: AsyncSession = Depends(get_db_session),
    container: Any = Depends(get_container),
) -> RouteAggregationService:
    return container.build_route_aggregation_service(session)
