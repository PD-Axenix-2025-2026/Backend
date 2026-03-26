from __future__ import annotations

from dataclasses import dataclass
from datetime import time
from decimal import Decimal
from zoneinfo import ZoneInfo

from app.models.enums import LocationType, TransportType

SEED_TIMEZONE = ZoneInfo("Europe/Moscow")
SEED_DATE_OFFSETS = (0, 1, 3, 7)


@dataclass(slots=True, frozen=True)
class LocationSeed:
    key: str
    code: str
    name: str
    location_type: LocationType
    city_name: str | None
    country_code: str
    lat: float
    lon: float
    timezone: str
    is_hub: bool = False
    parent_key: str | None = None


@dataclass(slots=True, frozen=True)
class CarrierSeed:
    key: str
    code: str
    name: str
    transport_type: TransportType
    website_url: str


@dataclass(slots=True, frozen=True)
class SegmentSeed:
    key: str
    origin_key: str
    destination_key: str
    carrier_key: str
    transport_type: TransportType
    segment_code: str
    departure_time: time
    duration_minutes: int
    price_amount: Decimal
    available_seats: int


def _city(
    *,
    key: str,
    code: str,
    name: str,
    lat: float,
    lon: float,
    is_hub: bool = False,
) -> LocationSeed:
    return LocationSeed(
        key=key,
        code=code,
        name=name,
        location_type=LocationType.city,
        city_name=name,
        country_code="RU",
        lat=lat,
        lon=lon,
        timezone="Europe/Moscow",
        is_hub=is_hub,
    )


def _child_location(
    *,
    key: str,
    code: str,
    name: str,
    location_type: LocationType,
    city_name: str,
    lat: float,
    lon: float,
    parent_key: str,
) -> LocationSeed:
    return LocationSeed(
        key=key,
        code=code,
        name=name,
        location_type=location_type,
        city_name=city_name,
        country_code="RU",
        lat=lat,
        lon=lon,
        timezone="Europe/Moscow",
        parent_key=parent_key,
    )


def _carrier(
    *,
    key: str,
    code: str,
    name: str,
    transport_type: TransportType,
    website_url: str,
) -> CarrierSeed:
    return CarrierSeed(
        key=key,
        code=code,
        name=name,
        transport_type=transport_type,
        website_url=website_url,
    )


def _segment(
    *,
    key: str,
    origin_key: str,
    destination_key: str,
    carrier_key: str,
    transport_type: TransportType,
    segment_code: str,
    departure_hour: int,
    departure_minute: int,
    duration_minutes: int,
    price_amount: str,
    available_seats: int,
) -> SegmentSeed:
    return SegmentSeed(
        key=key,
        origin_key=origin_key,
        destination_key=destination_key,
        carrier_key=carrier_key,
        transport_type=transport_type,
        segment_code=segment_code,
        departure_time=time(hour=departure_hour, minute=departure_minute),
        duration_minutes=duration_minutes,
        price_amount=Decimal(price_amount),
        available_seats=available_seats,
    )


CITY_LOCATION_SEEDS = (
    _city(
        key="city_moscow",
        code="MOW",
        name="Москва",
        lat=55.7558,
        lon=37.6176,
        is_hub=True,
    ),
    _city(
        key="city_spb",
        code="SPB",
        name="Санкт-Петербург",
        lat=59.9343,
        lon=30.3351,
        is_hub=True,
    ),
    _city(
        key="city_kazan",
        code="KZN",
        name="Казань",
        lat=55.7963,
        lon=49.1088,
    ),
    _city(
        key="city_rostov",
        code="ROV",
        name="Ростов-на-Дону",
        lat=47.2357,
        lon=39.7015,
    ),
    _city(
        key="city_taganrog",
        code="TGN",
        name="Таганрог",
        lat=47.2362,
        lon=38.8969,
    ),
    _city(
        key="city_sochi",
        code="SCH",
        name="Сочи",
        lat=43.5855,
        lon=39.7231,
    ),
)

TERMINAL_LOCATION_SEEDS = (
    _child_location(
        key="airport_svo",
        code="SVO",
        name="Шереметьево",
        location_type=LocationType.airport,
        city_name="Москва",
        lat=55.9726,
        lon=37.4146,
        parent_key="city_moscow",
    ),
    _child_location(
        key="airport_led",
        code="LED",
        name="Пулково",
        location_type=LocationType.airport,
        city_name="Санкт-Петербург",
        lat=59.8003,
        lon=30.2625,
        parent_key="city_spb",
    ),
    _child_location(
        key="station_mow_len",
        code="MOW-LEN",
        name="Ленинградский вокзал",
        location_type=LocationType.railway_station,
        city_name="Москва",
        lat=55.7764,
        lon=37.6559,
        parent_key="city_moscow",
    ),
    _child_location(
        key="station_spb_mosk",
        code="SPB-MOSK",
        name="Московский вокзал",
        location_type=LocationType.railway_station,
        city_name="Санкт-Петербург",
        lat=59.9301,
        lon=30.3613,
        parent_key="city_spb",
    ),
    _child_location(
        key="bus_mow_central",
        code="MOW-BUS",
        name="Центральный автовокзал",
        location_type=LocationType.bus_station,
        city_name="Москва",
        lat=55.8102,
        lon=37.7986,
        parent_key="city_moscow",
    ),
)

LOCATION_SEEDS = CITY_LOCATION_SEEDS + TERMINAL_LOCATION_SEEDS


CARRIER_SEEDS = (
    _carrier(
        key="carrier_aeroflot",
        code="SU",
        name="Aeroflot",
        transport_type=TransportType.plane,
        website_url="https://www.aeroflot.ru",
    ),
    _carrier(
        key="carrier_s7",
        code="S7",
        name="S7 Airlines",
        transport_type=TransportType.plane,
        website_url="https://www.s7.ru",
    ),
    _carrier(
        key="carrier_rzd",
        code="RZD",
        name="Russian Railways",
        transport_type=TransportType.train,
        website_url="https://www.rzd.ru",
    ),
    _carrier(
        key="carrier_bus",
        code="BUS",
        name="Intercity Bus",
        transport_type=TransportType.bus,
        website_url="https://example.com/bus",
    ),
)


MOSCOW_SPB_SEGMENT_SEEDS = (
    _segment(
        key="mow_spb_plane",
        origin_key="city_moscow",
        destination_key="city_spb",
        carrier_key="carrier_aeroflot",
        transport_type=TransportType.plane,
        segment_code="SU 1001",
        departure_hour=7,
        departure_minute=30,
        duration_minutes=95,
        price_amount="4600.00",
        available_seats=16,
    ),
    _segment(
        key="mow_spb_train",
        origin_key="city_moscow",
        destination_key="city_spb",
        carrier_key="carrier_rzd",
        transport_type=TransportType.train,
        segment_code="RZD 001",
        departure_hour=8,
        departure_minute=15,
        duration_minutes=250,
        price_amount="3300.00",
        available_seats=48,
    ),
    _segment(
        key="mow_spb_bus",
        origin_key="city_moscow",
        destination_key="city_spb",
        carrier_key="carrier_bus",
        transport_type=TransportType.bus,
        segment_code="BUS 101",
        departure_hour=9,
        departure_minute=0,
        duration_minutes=720,
        price_amount="1900.00",
        available_seats=30,
    ),
    _segment(
        key="spb_mow_plane",
        origin_key="city_spb",
        destination_key="city_moscow",
        carrier_key="carrier_aeroflot",
        transport_type=TransportType.plane,
        segment_code="SU 1002",
        departure_hour=18,
        departure_minute=20,
        duration_minutes=100,
        price_amount="4500.00",
        available_seats=15,
    ),
    _segment(
        key="spb_mow_train",
        origin_key="city_spb",
        destination_key="city_moscow",
        carrier_key="carrier_rzd",
        transport_type=TransportType.train,
        segment_code="RZD 002",
        departure_hour=20,
        departure_minute=45,
        duration_minutes=255,
        price_amount="3200.00",
        available_seats=44,
    ),
)

REGIONAL_SEGMENT_SEEDS = (
    _segment(
        key="mow_kzn_plane",
        origin_key="city_moscow",
        destination_key="city_kazan",
        carrier_key="carrier_s7",
        transport_type=TransportType.plane,
        segment_code="S7 2201",
        departure_hour=10,
        departure_minute=10,
        duration_minutes=100,
        price_amount="5200.00",
        available_seats=12,
    ),
    _segment(
        key="kzn_mow_plane",
        origin_key="city_kazan",
        destination_key="city_moscow",
        carrier_key="carrier_s7",
        transport_type=TransportType.plane,
        segment_code="S7 2202",
        departure_hour=17,
        departure_minute=40,
        duration_minutes=105,
        price_amount="5000.00",
        available_seats=11,
    ),
    _segment(
        key="rov_mow_plane",
        origin_key="city_rostov",
        destination_key="city_moscow",
        carrier_key="carrier_aeroflot",
        transport_type=TransportType.plane,
        segment_code="SU 3001",
        departure_hour=7,
        departure_minute=10,
        duration_minutes=110,
        price_amount="4200.00",
        available_seats=13,
    ),
    _segment(
        key="tgn_mow_bus",
        origin_key="city_taganrog",
        destination_key="city_moscow",
        carrier_key="carrier_bus",
        transport_type=TransportType.bus,
        segment_code="BUS 205",
        departure_hour=6,
        departure_minute=0,
        duration_minutes=1020,
        price_amount="2100.00",
        available_seats=27,
    ),
    _segment(
        key="mow_sch_plane",
        origin_key="city_moscow",
        destination_key="city_sochi",
        carrier_key="carrier_aeroflot",
        transport_type=TransportType.plane,
        segment_code="SU 4001",
        departure_hour=11,
        departure_minute=15,
        duration_minutes=165,
        price_amount="6900.00",
        available_seats=10,
    ),
    _segment(
        key="sch_mow_plane",
        origin_key="city_sochi",
        destination_key="city_moscow",
        carrier_key="carrier_aeroflot",
        transport_type=TransportType.plane,
        segment_code="SU 4002",
        departure_hour=19,
        departure_minute=25,
        duration_minutes=170,
        price_amount="6700.00",
        available_seats=10,
    ),
)

TERMINAL_SEGMENT_SEEDS = (
    _segment(
        key="svo_led_plane",
        origin_key="airport_svo",
        destination_key="airport_led",
        carrier_key="carrier_aeroflot",
        transport_type=TransportType.plane,
        segment_code="SU 1201",
        departure_hour=8,
        departure_minute=45,
        duration_minutes=90,
        price_amount="4800.00",
        available_seats=9,
    ),
    _segment(
        key="led_svo_plane",
        origin_key="airport_led",
        destination_key="airport_svo",
        carrier_key="carrier_aeroflot",
        transport_type=TransportType.plane,
        segment_code="SU 1202",
        departure_hour=17,
        departure_minute=10,
        duration_minutes=95,
        price_amount="4700.00",
        available_seats=9,
    ),
    _segment(
        key="mow_len_spb_mosk_train",
        origin_key="station_mow_len",
        destination_key="station_spb_mosk",
        carrier_key="carrier_rzd",
        transport_type=TransportType.train,
        segment_code="RZD 101",
        departure_hour=7,
        departure_minute=55,
        duration_minutes=245,
        price_amount="3400.00",
        available_seats=52,
    ),
    _segment(
        key="spb_mosk_mow_len_train",
        origin_key="station_spb_mosk",
        destination_key="station_mow_len",
        carrier_key="carrier_rzd",
        transport_type=TransportType.train,
        segment_code="RZD 102",
        departure_hour=18,
        departure_minute=5,
        duration_minutes=250,
        price_amount="3350.00",
        available_seats=52,
    ),
)

SEGMENT_SEEDS = (
    MOSCOW_SPB_SEGMENT_SEEDS + REGIONAL_SEGMENT_SEEDS + TERMINAL_SEGMENT_SEEDS
)


__all__ = [
    "CarrierSeed",
    "CARRIER_SEEDS",
    "CITY_LOCATION_SEEDS",
    "LOCATION_SEEDS",
    "LocationSeed",
    "MOSCOW_SPB_SEGMENT_SEEDS",
    "REGIONAL_SEGMENT_SEEDS",
    "SEED_DATE_OFFSETS",
    "SEED_TIMEZONE",
    "SEGMENT_SEEDS",
    "SegmentSeed",
    "TERMINAL_LOCATION_SEEDS",
    "TERMINAL_SEGMENT_SEEDS",
]
