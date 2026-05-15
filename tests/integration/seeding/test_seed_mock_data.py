from __future__ import annotations

import asyncio
import os
from datetime import date, timedelta
from pathlib import Path
from typing import Any

import app.main as app_main
import pytest
from app.core.config import Settings, get_settings
from app.core.database import (
    build_engine,
    build_session_factory,
    dispose_engine,
    init_models,
)
from app.models.enums import LocationType, TransportType
from app.seeding.mock_data import (
    SEED_DATE_OFFSETS,
    build_mock_data_bundle,
    seed_mock_data,
)
from httpx import ASGITransport, AsyncClient

BASE_DATE = date(2026, 4, 14)


def test_build_mock_data_bundle_matches_expected_dataset() -> None:
    bundle = build_mock_data_bundle(BASE_DATE)
    locations_by_code = {
        location.code: location
        for location in bundle.locations
        if location.code is not None
    }
    child_count = sum(
        1 for location in bundle.locations if location.parent_location_id is not None
    )
    location_types = {location.location_type for location in bundle.locations}
    moscow_id = locations_by_code["MOW"].id
    spb_id = locations_by_code["SPB"].id
    moscow_spb_segments = [
        segment
        for segment in bundle.route_segments
        if segment.origin_location_id == moscow_id
        and segment.destination_location_id == spb_id
    ]
    moscow_spb_dates = {segment.departure_at.date() for segment in moscow_spb_segments}
    moscow_spb_transport_types = {
        segment.transport_type
        for segment in moscow_spb_segments
        if segment.departure_at.date() == BASE_DATE
    }

    assert len(bundle.locations) == 11
    assert len(bundle.carriers) == 4
    assert len(bundle.route_segments) == 60
    assert child_count == 5
    assert location_types == {
        LocationType.city,
        LocationType.airport,
        LocationType.railway_station,
        LocationType.bus_station,
    }
    assert moscow_spb_dates == {
        BASE_DATE + timedelta(days=offset) for offset in SEED_DATE_OFFSETS
    }
    assert moscow_spb_transport_types == {
        TransportType.plane,
        TransportType.train,
        TransportType.bus,
    }


@pytest.mark.asyncio
async def test_seeded_database_supports_search_api_flow(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    if os.getenv("PDAXENIX_RUN_SEED_SMOKE") != "1":
        pytest.skip(
            "Seed DB smoke is disabled by default. "
            "Enable it with PDAXENIX_RUN_SEED_SMOKE=1 on a local machine."
        )

    database_url = _sqlite_database_url(tmp_path)
    settings = Settings(database_url=database_url, redis_url=None)
    engine = build_engine(settings)
    session_factory = build_session_factory(engine)

    try:
        await init_models(engine)
        await seed_mock_data(session_factory, base_date=BASE_DATE)
    finally:
        await dispose_engine(engine)

    monkeypatch.setenv("PDAXENIX_DATABASE_URL", database_url)
    monkeypatch.setenv("PDAXENIX_APP_ENV", "test")
    monkeypatch.delenv("PDAXENIX_REDIS_URL", raising=False)
    get_settings.cache_clear()
    application = app_main.create_app()

    try:
        async with application.router.lifespan_context(application):
            async with AsyncClient(
                transport=ASGITransport(app=application),
                base_url="http://testserver",
            ) as client:
                moscow_response = await client.get(
                    "/api/locations",
                    params={"prefix": "Мос", "types": "city", "limit": 10},
                )
                spb_response = await client.get(
                    "/api/locations",
                    params={"prefix": "Санкт", "types": "city", "limit": 10},
                )

                assert moscow_response.status_code == 200
                assert spb_response.status_code == 200

                origin_id = moscow_response.json()["items"][0]["id"]
                destination_id = spb_response.json()["items"][0]["id"]

                create_response = await client.post(
                    "/api/searches",
                    json={
                        "origin": {"id": origin_id, "type": "city"},
                        "destination": {"id": destination_id, "type": "city"},
                        "date": BASE_DATE.isoformat(),
                        "passengers": {
                            "adults": 1,
                            "children": 0,
                            "infants": 0,
                        },
                        "transport_types": ["plane", "train", "bus"],
                        "preferences": {"sort": "best", "max_transfers": 0},
                    },
                )

                assert create_response.status_code == 201
                search_id = create_response.json()["search_id"]

                results_body: dict[str, Any] | None = None
                for _ in range(20):
                    results_response = await client.get(
                        f"/api/searches/{search_id}/results",
                        params={
                            "sort": "price",
                            "transport_types": "plane,train,bus",
                            "limit": 10,
                            "offset": 0,
                        },
                    )
                    assert results_response.status_code == 200
                    results_body = results_response.json()
                    if results_body["is_complete"] and results_body["items"]:
                        break
                    await asyncio.sleep(0)

                assert results_body is not None
                assert results_body["is_complete"] is True
                assert results_body["meta"]["total_found"] >= 3
                assert len(results_body["items"]) >= 1

                route_id = results_body["items"][0]["route_id"]
                detail_response = await client.get(f"/api/routes/{route_id}")
                checkout_response = await client.post(
                    f"/api/routes/{route_id}/checkout-link",
                    json={"provider_offer_id": "seed-offer-1"},
                )

                assert detail_response.status_code == 200
                assert detail_response.json()["source"] == "database"
                assert checkout_response.status_code == 200
                assert checkout_response.json()["method"] == "GET"
                assert "route_id=" in checkout_response.json()["url"]
    finally:
        get_settings.cache_clear()


def _sqlite_database_url(tmp_path: Path) -> str:
    return f"sqlite+aiosqlite:///{tmp_path / 'seeded.sqlite3'}"
