from datetime import date
from uuid import uuid4

import pytest
from app.core.container import AppContainer
from app.models.enums import LocationType
from app.services.contracts import RouteSearchCriteria
from app.services.use_cases import CreateSearchUseCase, ListLocationsUseCase
from fastapi import FastAPI


@pytest.mark.asyncio
async def test_container_builds_use_cases(app: FastAPI) -> None:
    async with app.router.lifespan_context(app):
        container = app.state.container

        assert isinstance(container, AppContainer)
        assert isinstance(container.list_locations_use_case, ListLocationsUseCase)
        assert isinstance(container.create_search_use_case, CreateSearchUseCase)

        locations = await container.list_locations_use_case.execute(prefix="Mo")
        route_candidates = await container.route_search.search(
            RouteSearchCriteria(
                origin_id=uuid4(),
                origin_type=LocationType.city,
                destination_id=uuid4(),
                destination_type=LocationType.city,
                travel_date=date(2026, 3, 10),
            )
        )

    assert locations == []
    assert route_candidates == []
