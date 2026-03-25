from datetime import date
from uuid import uuid4

import pytest
from app.core.container import AppContainer
from app.models.enums import LocationType
from app.services.contracts import RouteSearchCriteria
from app.services.route_aggregation import RouteAggregationService
from fastapi import FastAPI


@pytest.mark.asyncio
async def test_container_builds_route_service(app: FastAPI) -> None:
    async with app.router.lifespan_context(app):
        container = app.state.container

        assert isinstance(container, AppContainer)

        async with container.session_factory() as session:
            service = container.build_route_aggregation_service(session)

            assert isinstance(service, RouteAggregationService)

            results = await service.search(
                RouteSearchCriteria(
                    origin_id=uuid4(),
                    origin_type=LocationType.city,
                    destination_id=uuid4(),
                    destination_type=LocationType.city,
                    travel_date=date(2026, 3, 10),
                )
            )

    assert results == []
