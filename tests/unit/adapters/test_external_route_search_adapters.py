from __future__ import annotations

from decimal import Decimal
from uuid import uuid4

from app.adapters.rzd_route_search import RzdRouteSearchAdapter
from app.adapters.yandex_route_search import YandexRaspRouteSearchAdapter
from app.models.location import Location
from app.services.search.contracts import ProviderRouteSegment, RouteCandidate
from app.services.search.snapshot_builder import build_route_snapshot

from tests.support.route_search import build_location


def test_yandex_adapter_parses_direct_and_transfer_routes() -> None:
    requested_origin = build_location(
        code="MOW",
        provider_code="c213",
        name="Москва",
    )
    requested_destination = build_location(
        code="SPB",
        provider_code="c2",
        name="Санкт-Петербург",
    )
    hub = build_location(
        code="KZN",
        provider_code="c43",
        name="Казань",
    )
    adapter = YandexRaspRouteSearchAdapter(
        api_key="test-key",
        database_session_factory=None,  # type: ignore[arg-type]
    )

    routes = adapter._parse_response(
        _build_yandex_response_with_price(),
        requested_origin=requested_origin,
        requested_destination=requested_destination,
        requested_origin_code="c213",
        requested_destination_code="c2",
        locations_by_code={
            "c213": requested_origin,
            "c2": requested_destination,
            "c43": hub,
        },
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
    assert snapshot.total_price is not None
    assert snapshot.total_price.amount == Decimal("7300")
    assert snapshot.segments[0].price is None
    assert snapshot.segments[1].price is None


def test_yandex_adapter_keeps_transfer_route_without_total_price() -> None:
    requested_origin = build_location(
        code="KGD",
        provider_code="c22",
        name="Калининград",
    )
    requested_destination = build_location(
        code="TYM",
        provider_code="c55",
        name="Тюмень",
    )
    hub = build_location(
        code="MOW",
        provider_code="c213",
        name="Москва",
    )
    adapter = YandexRaspRouteSearchAdapter(
        api_key="test-key",
        database_session_factory=None,  # type: ignore[arg-type]
    )

    routes = adapter._parse_response(
        _build_yandex_response_without_total_price(),
        requested_origin=requested_origin,
        requested_destination=requested_destination,
        requested_origin_code="c22",
        requested_destination_code="c55",
        locations_by_code={
            "c22": requested_origin,
            "c213": hub,
            "c55": requested_destination,
        },
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
    assert snapshot.total_price is None
    assert snapshot.segments[0].price is None
    assert snapshot.segments[1].price is None


def test_rzd_adapter_parses_direct_and_transfer_routes() -> None:
    requested_origin = build_location(
        code="MOW",
        provider_code="2000000",
        name="Москва",
    )
    requested_destination = build_location(
        code="SPB",
        provider_code="2004000",
        name="Санкт-Петербург",
    )
    hub = build_location(
        code="KZN",
        provider_code="2060615",
        name="Казань",
    )
    adapter = RzdRouteSearchAdapter(
        http_client_factory=None,  # type: ignore[arg-type]
        database_session_factory=None,  # type: ignore[arg-type]
    )

    routes = adapter._parse_routes_response(
        _build_rzd_response(),
        requested_origin=requested_origin,
        requested_destination=requested_destination,
        requested_origin_code="2000000",
        requested_destination_code="2004000",
        locations_by_code={
            "2000000": requested_origin,
            "2004000": requested_destination,
            "2060615": hub,
        },
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
    assert second_leg.destination_location.id == requested_destination.id


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


def _build_rzd_response() -> dict[str, object]:
    return {
        "tp": [
            {
                "list": [
                    {
                        "route0": "2000000",
                        "route1": "2004000",
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
                                "route0": "2000000",
                                "route1": "2060615",
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
                                "route0": "2060615",
                                "route1": "2004000",
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
                        "route0": "2000000",
                        "route1": "2004000",
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
