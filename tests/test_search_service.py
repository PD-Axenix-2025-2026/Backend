from __future__ import annotations

import logging
from collections.abc import AsyncIterator
from datetime import date, timedelta
from decimal import Decimal
from uuid import uuid4

import pytest
import pytest_asyncio
from app.models.enums import LocationType, TransportType
from app.services.contracts import (
    RouteSearchCriteria,
    SearchResultsQuery,
    SearchSortOption,
    SearchStatus,
)
from app.services.search_store import InMemorySearchStore
from app.services.search_store_models import SearchNotFoundError, utc_now
from app.services.search_validation import SearchValidationError

from tests.search_service_support import (
    SearchFixture,
    build_location,
    build_search_fixture,
    build_search_service,
    create_completed_search,
)


@pytest_asyncio.fixture
async def search_fixture() -> AsyncIterator[SearchFixture]:
    fixture = build_search_fixture()
    try:
        yield fixture
    finally:
        await fixture.service.shutdown()


@pytest.mark.asyncio
async def test_search_service_completes_search_and_marks_best_route(
    search_fixture: SearchFixture,
) -> None:
    search_id, completion_status = await create_completed_search(search_fixture)
    page = await search_fixture.service.get_results(search_id, SearchResultsQuery())

    assert completion_status == SearchStatus.complete
    assert search_fixture.aggregation_service.calls == [search_fixture.criteria]
    assert page.total_found == 2
    assert page.items[0].labels == ("best", "direct")
    assert page.items[0].route.total_price.amount == Decimal("3800.00")
    assert page.items[1].labels == ("direct",)


@pytest.mark.asyncio
async def test_search_service_filters_results_and_builds_checkout_link(
    search_fixture: SearchFixture,
) -> None:
    search_id, _completion_status = await create_completed_search(search_fixture)
    page = await search_fixture.service.get_results(search_id, SearchResultsQuery())
    filtered_page = await search_fixture.service.get_results(
        search_id,
        SearchResultsQuery(
            sort=SearchSortOption.price,
            transport_types=(TransportType.train,),
            max_price=Decimal("5000.00"),
        ),
    )
    route = await search_fixture.service.get_route_detail(page.items[0].route.route_id)
    checkout = await search_fixture.service.build_checkout_link(
        route.route_id,
        provider_offer_id="offer-1",
    )

    assert filtered_page.total_found == 1
    assert filtered_page.items[0].route.transport_types == (TransportType.train,)
    assert filtered_page.items[0].labels == ("direct",)
    assert route.source == "database"
    assert "provider_offer_id=offer-1" in checkout.url


@pytest.mark.asyncio
async def test_search_service_logs_search_lifecycle(
    caplog: pytest.LogCaptureFixture,
    search_fixture: SearchFixture,
) -> None:
    caplog.set_level(logging.INFO, logger="app.services.search_service")
    caplog.set_level(logging.INFO, logger="app.services.search_store")
    search_id, completion_status = await create_completed_search(search_fixture)

    search_service_records = [
        record
        for record in caplog.records
        if record.name == "app.services.search_service"
    ]
    search_store_records = [
        record
        for record in caplog.records
        if record.name == "app.services.search_store"
    ]

    assert completion_status == SearchStatus.complete
    assert any(
        "Search created" in record.getMessage()
        and getattr(record, "search_id", None) == str(search_id)
        for record in search_service_records
    )
    assert any(
        "Background search completed" in record.getMessage()
        and getattr(record, "search_id", None) == str(search_id)
        for record in search_service_records
    )
    assert any(
        "Search record marked complete" in record.getMessage()
        and getattr(record, "search_id", None) == str(search_id)
        for record in search_store_records
    )


@pytest.mark.asyncio
async def test_search_service_rejects_invalid_locations() -> None:
    origin = build_location(
        location_id=uuid4(),
        code="MOW",
        name="Moscow",
        location_type=LocationType.city,
    )
    service, _aggregation_service = build_search_service(
        locations={origin.id: origin},
        segments={},
        candidates=[],
    )
    criteria = RouteSearchCriteria(
        origin_id=origin.id,
        origin_type=origin.location_type,
        destination_id=uuid4(),
        destination_type=LocationType.city,
        travel_date=date(2026, 4, 14),
    )

    try:
        with pytest.raises(SearchValidationError):
            await service.create_search(criteria)
    finally:
        await service.shutdown()


@pytest.mark.asyncio
async def test_search_store_drops_expired_searches() -> None:
    store = InMemorySearchStore()
    criteria = RouteSearchCriteria(
        origin_id=uuid4(),
        origin_type=LocationType.city,
        destination_id=uuid4(),
        destination_type=LocationType.city,
        travel_date=date(2026, 4, 14),
    )
    search_id = uuid4()
    await store.create_search(
        search_id=search_id,
        criteria=criteria,
        expires_at=utc_now() - timedelta(seconds=1),
    )

    with pytest.raises(SearchNotFoundError):
        await store.get_search(search_id)
