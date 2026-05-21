from __future__ import annotations

from decimal import Decimal
from typing import Any
from uuid import uuid4

import pytest
from app.adapters.rzd_route_search import RzdRouteSearchAdapter
from app.adapters.yandex_route_search import YandexRaspRouteSearchAdapter
from app.models.location import Location
from app.services.search.contracts import (
    ProviderRouteSegment,
    RouteCandidate,
    RouteSearchCriteria,
)
from app.services.search.snapshot_builder import build_route_snapshot

from tests.support.route_search import build_location, build_search_criteria


def test_yandex_adapter_parses_direct_and_transfer_routes() -> None:
    requested_origin, requested_destination, hub = _build_yandex_moscow_spb_locations()

    routes = _build_yandex_adapter()._parse_response(
        _build_yandex_response_with_price(),
        requested_origin=requested_origin,
        requested_destination=requested_destination,
        requested_origin_code="c213",
        requested_destination_code="c2",
        locations_by_code=_index_locations_by_provider_code(
            requested_origin,
            requested_destination,
            hub,
        ),
    )

    assert len(routes) == 2

    direct_candidate, transfer_candidate = routes
    assert direct_candidate.transfers == 0
    assert transfer_candidate.transfers == 1
    assert transfer_candidate.total_price == Decimal("7300")

    _assert_transfer_candidate_segments(
        transfer_candidate=transfer_candidate,
        requested_origin=requested_origin,
        hub=hub,
        requested_destination=requested_destination,
    )

    snapshot = build_route_snapshot(
        search_id=uuid4(),
        candidate=transfer_candidate,
        segments=transfer_candidate.resolved_segments,
    )
    _assert_transfer_snapshot_prices(
        total_price=snapshot.total_price.amount if snapshot.total_price else None,
        first_segment_price=snapshot.segments[0].price,
        second_segment_price=snapshot.segments[1].price,
    )


def test_yandex_adapter_keeps_transfer_route_without_total_price() -> None:
    requested_origin, requested_destination, hub = _build_yandex_kgd_tym_locations()

    routes = _build_yandex_adapter()._parse_response(
        _build_yandex_response_without_total_price(),
        requested_origin=requested_origin,
        requested_destination=requested_destination,
        requested_origin_code="c22",
        requested_destination_code="c55",
        locations_by_code=_index_locations_by_provider_code(
            requested_origin,
            requested_destination,
            hub,
        ),
    )

    assert len(routes) == 1

    transfer_candidate = routes[0]
    assert transfer_candidate.transfers == 1
    assert transfer_candidate.total_price is None

    snapshot = build_route_snapshot(
        search_id=uuid4(),
        candidate=transfer_candidate,
        segments=transfer_candidate.resolved_segments,
    )
    _assert_transfer_snapshot_prices(
        total_price=None,
        first_segment_price=snapshot.segments[0].price,
        second_segment_price=snapshot.segments[1].price,
    )


def test_rzd_adapter_parses_direct_and_transfer_routes() -> None:
    requested_origin, requested_destination, hub = _build_rzd_moscow_spb_locations()

    routes = _build_rzd_adapter()._parse_routes_response(
        _build_rzd_response(),
        requested_origin=requested_origin,
        requested_destination=requested_destination,
        requested_origin_code="2000000",
        requested_destination_code="2004000",
        locations_by_code=_index_locations_by_provider_code(
            requested_origin,
            requested_destination,
            hub,
        ),
    )

    assert len(routes) == 2

    direct_candidate, transfer_candidate = routes
    assert direct_candidate.total_price == Decimal("3300")
    assert transfer_candidate.transfers == 1
    assert transfer_candidate.total_price == Decimal("7300")
    assert len(transfer_candidate.resolved_segments) == 2

    first_leg = transfer_candidate.resolved_segments[0]
    second_leg = transfer_candidate.resolved_segments[1]
    assert isinstance(first_leg, ProviderRouteSegment)
    assert first_leg.origin_location.id == requested_origin.id
    assert first_leg.destination_location.id == hub.id
    assert first_leg.departure_at.tzinfo is not None
    assert first_leg.arrival_at.tzinfo is not None
    assert second_leg.destination_location.id == requested_destination.id
    assert second_leg.departure_at.tzinfo is not None
    assert second_leg.arrival_at.tzinfo is not None


@pytest.mark.asyncio
async def test_rzd_search_requests_direct_and_transfer_routes() -> None:
    requested_origin, requested_destination, hub = _build_rzd_moscow_spb_locations()
    criteria = _build_transfer_search_criteria(
        origin=requested_origin,
        destination=requested_destination,
    )
    adapter = _StubRzdRouteSearchAdapter(
        requested_origin=requested_origin,
        requested_destination=requested_destination,
        locations=(requested_origin, requested_destination, hub),
        responses_by_md={
            0: _build_rzd_direct_only_response(),
            1: _build_rzd_transfer_only_response(),
        },
    )

    routes = await adapter.search(criteria)

    assert adapter.md_calls == [0, 1]
    assert [(candidate.transfers, candidate.total_price) for candidate in routes] == [
        (0, Decimal("3300")),
        (1, Decimal("7300")),
    ]


@pytest.mark.asyncio
async def test_rzd_search_keeps_successful_results_when_transfer_request_fails() -> (
    None
):
    requested_origin, requested_destination, _ = _build_rzd_moscow_spb_locations()
    criteria = _build_transfer_search_criteria(
        origin=requested_origin,
        destination=requested_destination,
    )
    adapter = _StubRzdRouteSearchAdapter(
        requested_origin=requested_origin,
        requested_destination=requested_destination,
        locations=(requested_origin, requested_destination),
        responses_by_md={
            0: _build_rzd_direct_only_response(),
            1: RuntimeError("transfer request failed"),
        },
    )

    routes = await adapter.search(criteria)

    assert adapter.md_calls == [0, 1]
    assert [(candidate.transfers, candidate.total_price) for candidate in routes] == [
        (0, Decimal("3300")),
    ]


def _assert_transfer_candidate_segments(
    *,
    transfer_candidate: RouteCandidate,
    requested_origin: Location,
    hub: Location,
    requested_destination: Location,
) -> None:
    assert len(transfer_candidate.resolved_segments) == 2

    first_leg = transfer_candidate.resolved_segments[0]
    second_leg = transfer_candidate.resolved_segments[1]
    assert isinstance(first_leg, ProviderRouteSegment)
    assert first_leg.origin_location.id == requested_origin.id
    assert first_leg.destination_location.id == hub.id
    assert first_leg.price_amount is None
    assert second_leg.destination_location.id == requested_destination.id
    assert second_leg.price_amount is None


def _assert_transfer_snapshot_prices(
    *,
    total_price: Decimal | None,
    first_segment_price: object,
    second_segment_price: object,
) -> None:
    assert total_price in {Decimal("7300"), None}
    assert first_segment_price is None
    assert second_segment_price is None


def _build_yandex_adapter() -> YandexRaspRouteSearchAdapter:
    return YandexRaspRouteSearchAdapter(
        api_key="test-key",
        database_session_factory=None,  # type: ignore[arg-type]
    )


def _build_rzd_adapter() -> RzdRouteSearchAdapter:
    return RzdRouteSearchAdapter(
        http_client_factory=None,  # type: ignore[arg-type]
        database_session_factory=None,  # type: ignore[arg-type]
    )


def _build_yandex_moscow_spb_locations() -> tuple[Location, Location, Location]:
    return (
        build_location(code="MOW", provider_code="c213", name="Москва"),
        build_location(code="SPB", provider_code="c2", name="Санкт-Петербург"),
        build_location(code="KZN", provider_code="c43", name="Казань"),
    )


def _build_yandex_kgd_tym_locations() -> tuple[Location, Location, Location]:
    return (
        build_location(code="KGD", provider_code="c22", name="Калининград"),
        build_location(code="TYM", provider_code="c55", name="Тюмень"),
        build_location(code="MOW", provider_code="c213", name="Москва"),
    )


def _build_rzd_moscow_spb_locations() -> tuple[Location, Location, Location]:
    return (
        build_location(code="MOW", provider_code="2000000", name="Москва"),
        build_location(code="SPB", provider_code="2004000", name="Санкт-Петербург"),
        build_location(code="KZN", provider_code="2060615", name="Казань"),
    )


def _build_transfer_search_criteria(
    *,
    origin: Location,
    destination: Location,
) -> RouteSearchCriteria:
    return build_search_criteria(
        origin_id=origin.id,
        origin_type=origin.location_type,
        destination_id=destination.id,
        destination_type=destination.location_type,
        max_transfers=3,
    )


def _index_locations_by_provider_code(*locations: Location) -> dict[str, Location]:
    indexed_locations: dict[str, Location] = {}
    for location in locations:
        provider_code = location.rzd_code or location.yandex_code
        if provider_code is not None:
            indexed_locations[provider_code] = location
    return indexed_locations


class _StubRzdRouteSearchAdapter(RzdRouteSearchAdapter):
    def __init__(
        self,
        *,
        requested_origin: Location,
        requested_destination: Location,
        locations: tuple[Location, ...],
        responses_by_md: dict[int, dict[str, object] | Exception],
    ) -> None:
        super().__init__(
            http_client_factory=None,  # type: ignore[arg-type]
            database_session_factory=None,  # type: ignore[arg-type]
        )
        self._requested_origin = requested_origin
        self._requested_destination = requested_destination
        self._locations_by_code = _index_locations_by_provider_code(*locations)
        self._responses_by_md = responses_by_md
        self.md_calls: list[int] = []

    async def _load_search_inputs(
        self,
        criteria: RouteSearchCriteria,
    ) -> tuple[Location | None, Location | None, str | None, str | None]:
        return (
            self._requested_origin,
            self._requested_destination,
            self._requested_origin.rzd_code,
            self._requested_destination.rzd_code,
        )

    async def _load_locations_by_codes(
        self,
        codes: tuple[str, ...],
    ) -> dict[str, Location]:
        return {
            code: self._locations_by_code[code]
            for code in codes
            if code in self._locations_by_code
        }

    async def _fetch_routes(
        self,
        params: dict[str, int | str],
    ) -> object:
        md = int(params["md"])
        self.md_calls.append(md)
        response = self._responses_by_md[md]
        if isinstance(response, Exception):
            raise response
        return response


def _build_yandex_response_with_price() -> dict[str, object]:
    return {
        "segments": [
            {
                "from": {"code": "c213", "title": "Москва"},
                "to": {"code": "c2", "title": "Санкт-Петербург"},
                "departure": "2026-05-14T10:00:00+03:00",
                "arrival": "2026-05-14T11:30:00+03:00",
                "duration": 5400,
                "thread": {
                    "number": "SU 100",
                    "uid": "su-100",
                    "transport_type": "plane",
                    "carrier": {"code": "SU", "title": "Aeroflot"},
                },
                "tickets_info": {
                    "places": [
                        {
                            "currency": "RUB",
                            "price": {"whole": 4800, "cents": 0},
                        }
                    ]
                },
                "has_transfers": False,
            },
            {
                "has_transfers": True,
                "duration": 21600,
                "tickets_info": {
                    "places": [
                        {
                            "currency": "RUB",
                            "price": {"whole": 7300, "cents": 0},
                        }
                    ]
                },
                "details": [
                    {
                        "from": {"code": "c213", "title": "Москва"},
                        "to": {"code": "c43", "title": "Казань"},
                        "departure": "2026-05-14T10:00:00+03:00",
                        "arrival": "2026-05-14T11:40:00+03:00",
                        "duration": 6000,
                        "thread": {
                            "number": "S7 2201",
                            "uid": "s7-2201",
                            "transport_type": "plane",
                            "carrier": {"code": "S7", "title": "S7"},
                        },
                    },
                    {
                        "is_transfer": True,
                        "duration": 1800,
                        "transfer_point": {"code": "c43", "title": "Казань"},
                    },
                    {
                        "from": {"code": "c43", "title": "Казань"},
                        "to": {"code": "c2", "title": "Санкт-Петербург"},
                        "departure": "2026-05-14T13:10:00+03:00",
                        "arrival": "2026-05-14T16:10:00+03:00",
                        "duration": 10800,
                        "thread": {
                            "number": "RZD 300",
                            "uid": "rzd-300",
                            "transport_type": "train",
                            "carrier": {"code": "RZD", "title": "RZD"},
                        },
                    },
                ],
            },
            {
                "has_transfers": True,
                "from": {"code": "c213", "title": "Москва"},
                "to": {"code": "c2", "title": "Санкт-Петербург"},
                "departure": "2026-05-14T10:00:00+03:00",
                "arrival": "2026-05-14T18:00:00+03:00",
                "duration": 28800,
            },
        ]
    }


def _build_yandex_response_without_total_price() -> dict[str, object]:
    return {
        "segments": [
            {
                "has_transfers": True,
                "duration": 40500,
                "details": [
                    {
                        "from": {"code": "c22", "title": "Калининград"},
                        "to": {"code": "c213", "title": "Москва"},
                        "departure": "2026-05-20T17:00:00+02:00",
                        "arrival": "2026-05-20T20:35:00+03:00",
                        "duration": 9300,
                        "thread": {
                            "number": "SU 1025",
                            "uid": "su-1025",
                            "transport_type": "plane",
                            "carrier": {"code": "SU", "title": "Aeroflot"},
                        },
                    },
                    {
                        "is_transfer": True,
                        "duration": 11100,
                        "transfer_point": {"code": "c213", "title": "Москва"},
                    },
                    {
                        "from": {"code": "c213", "title": "Москва"},
                        "to": {"code": "c55", "title": "Тюмень"},
                        "departure": "2026-05-20T23:40:00+03:00",
                        "arrival": "2026-05-21T04:15:00+05:00",
                        "duration": 9300,
                        "thread": {
                            "number": "FV 6365",
                            "uid": "fv-6365",
                            "transport_type": "plane",
                            "carrier": {"code": "FV", "title": "Россия"},
                        },
                    },
                ],
            }
        ]
    }


def _build_rzd_response() -> dict[str, Any]:
    return {
        "tp": [
            {
                "list": [
                    {
                        "code0": "2000000",
                        "code1": "2004000",
                        "station0": "Москва",
                        "station1": "Санкт-Петербург",
                        "date0": "14.05.2026",
                        "date1": "14.05.2026",
                        "time0": "10:00",
                        "time1": "14:10",
                        "timeInWay": "4:10",
                        "number": "RZD 001",
                        "carrier": "RZD",
                        "cars": [{"tariff": "3300", "freeSeats": 12}],
                    },
                    {
                        "transfers": 1,
                        "segments": [
                            {
                                "code0": "2000000",
                                "code1": "2060615",
                                "station0": "Москва",
                                "station1": "Казань",
                                "date0": "14.05.2026",
                                "date1": "14.05.2026",
                                "time0": "10:00",
                                "time1": "11:40",
                                "timeInWay": "1:40",
                                "number": "S7 2201",
                                "carrier": "S7",
                                "cars": [{"tariff": "5200", "freeSeats": 8}],
                            },
                            {
                                "code0": "2060615",
                                "code1": "2004000",
                                "station0": "Казань",
                                "station1": "Санкт-Петербург",
                                "date0": "14.05.2026",
                                "date1": "14.05.2026",
                                "time0": "13:10",
                                "time1": "16:10",
                                "timeInWay": "3:00",
                                "number": "RZD 300",
                                "carrier": "RZD",
                                "cars": [{"tariff": "2100", "freeSeats": 20}],
                            },
                        ],
                    },
                    {
                        "transfers": 1,
                        "code0": "2000000",
                        "code1": "2004000",
                        "station0": "Москва",
                        "station1": "Санкт-Петербург",
                        "date0": "14.05.2026",
                        "date1": "14.05.2026",
                        "time0": "10:00",
                        "time1": "18:00",
                    },
                ]
            }
        ]
    }


def _build_rzd_direct_only_response() -> dict[str, object]:
    return {
        "tp": [
            {
                "list": [_build_rzd_response()["tp"][0]["list"][0]],
            }
        ]
    }


def _build_rzd_transfer_only_response() -> dict[str, object]:
    return {
        "tp": [
            {
                "list": [_build_rzd_response()["tp"][0]["list"][1]],
            }
        ]
    }
