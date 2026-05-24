from __future__ import annotations

from collections import defaultdict
from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from datetime import date, datetime, time, timedelta
from decimal import Decimal, ROUND_HALF_UP
from random import Random

from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.models.carrier import Carrier
from app.models.enums import LocationType, TransportType
from app.models.location import Location
from app.models.route_segment import RouteSegment
from app.seeding.builders import stable_uuid

DEFAULT_LOAD_TEST_BASE_DATE = date(2026, 5, 22)
DEFAULT_LOAD_TEST_DAYS = 30
DEFAULT_LOAD_TEST_TARGET_SEGMENTS = 5_000
DEFAULT_LOAD_TEST_RANDOM_SEED = 20260522
DEFAULT_LOAD_TEST_BATCH_SIZE = 2_000
SOURCE_SYSTEM_NAME = "load_test_seed"
DIRECT_DEPARTURE_WINDOW_MINUTES = 16 * 60
TRANSFER_DEPARTURE_WINDOW_MINUTES = 14 * 60
MIN_TRANSFER_LAYOVER_MINUTES = 60
MAX_TRANSFER_LAYOVER_MINUTES = 720
RUB_QUANTIZER = Decimal("0.01")
TRANSPORT_TYPE_ORDER = (
    TransportType.plane,
    TransportType.train,
    TransportType.bus,
)


@dataclass(slots=True, frozen=True)
class LoadTestBundle:
    route_segments: tuple[RouteSegment, ...]
    base_date: date
    days: int
    target_segments: int
    direct_segments: int
    transfer_segments: int
    transfer_routes: int


@dataclass(slots=True, frozen=True)
class LoadTestStats:
    route_segments: int
    direct_segments: int
    transfer_segments: int
    transfer_routes: int
    base_date: date
    days: int


async def seed_load_test_data(
    session_factory: async_sessionmaker[AsyncSession],
    *,
    base_date: date | None = None,
    days: int = DEFAULT_LOAD_TEST_DAYS,
    target_segments: int = DEFAULT_LOAD_TEST_TARGET_SEGMENTS,
    random_seed: int = DEFAULT_LOAD_TEST_RANDOM_SEED,
    batch_size: int = DEFAULT_LOAD_TEST_BATCH_SIZE,
    append: bool = False,
) -> LoadTestStats:
    async with session_factory() as session:
        locations, carriers = await _load_seed_inputs(session)
        bundle = build_load_test_data_bundle(
            base_date=base_date,
            days=days,
            target_segments=target_segments,
            random_seed=random_seed,
            locations=locations,
            carriers=carriers,
        )

        if not append:
            await session.execute(delete(RouteSegment))

        await _insert_route_segments(
            session,
            bundle.route_segments,
            batch_size=batch_size,
        )
        await session.commit()

    return LoadTestStats(
        route_segments=len(bundle.route_segments),
        direct_segments=bundle.direct_segments,
        transfer_segments=bundle.transfer_segments,
        transfer_routes=bundle.transfer_routes,
        base_date=bundle.base_date,
        days=bundle.days,
    )


def build_load_test_data_bundle(
    *,
    locations: Sequence[Location],
    carriers: Sequence[Carrier],
    base_date: date | None = None,
    days: int = DEFAULT_LOAD_TEST_DAYS,
    target_segments: int = DEFAULT_LOAD_TEST_TARGET_SEGMENTS,
    random_seed: int = DEFAULT_LOAD_TEST_RANDOM_SEED,
) -> LoadTestBundle:
    if days <= 0:
        raise ValueError("days must be positive")
    if target_segments <= 0:
        raise ValueError("target_segments must be positive")

    reference_date = DEFAULT_LOAD_TEST_BASE_DATE if base_date is None else base_date
    usable_locations = _select_usable_locations(locations)
    if not usable_locations:
        raise ValueError("No usable locations were found for load-test generation")

    carriers_by_transport_type = _build_carriers_by_transport_type(carriers)
    if not carriers_by_transport_type:
        raise ValueError("No active carriers were found for load-test generation")

    origin_city = _find_location_by_code(usable_locations, "MOW")
    destination_city = _find_location_by_code(usable_locations, "SPB")

    rng = Random(random_seed)
    transfer_segments_target = target_segments - (target_segments // 2)
    transfer_segments_target -= transfer_segments_target % 2
    guaranteed_direct_segments = days
    direct_segments_target = (
        target_segments - transfer_segments_target - guaranteed_direct_segments
    )
    if direct_segments_target < 0:
        raise ValueError(
            "target_segments is too small to reserve one guaranteed Moscow-SPB route per day"
        )
    transfer_routes_target = transfer_segments_target // 2

    direct_day_quotas = _split_evenly(direct_segments_target, days)
    transfer_day_quotas = _split_evenly(transfer_routes_target, days)

    city_locations = tuple(
        location
        for location in usable_locations
        if location.location_type == LocationType.city
    )
    origin_pool = city_locations or usable_locations
    destination_pool = city_locations or usable_locations
    transfer_pool = city_locations or usable_locations

    route_segments: list[RouteSegment] = []
    for day_index in range(days):
        travel_date = reference_date + timedelta(days=day_index)

        guaranteed_departure_at = _build_departure_at(
            travel_date=travel_date,
            day_index=day_index,
            route_index=0,
            window_minutes=DIRECT_DEPARTURE_WINDOW_MINUTES,
            anchor_hour=7,
            salt=7,
        )
        route_segments.append(
            _build_route_segment(
                key=(
                    "guaranteed-direct",
                    reference_date,
                    day_index,
                    origin_city.code,
                    destination_city.code,
                ),
                origin=origin_city,
                destination=destination_city,
                carrier=_pick_carrier(
                    carriers_by_transport_type,
                    transport_type=TransportType.plane,
                    day_index=day_index,
                    route_index=0,
                ),
                transport_type=TransportType.plane,
                segment_code=_segment_code(
                    prefix="MOW-SPB",
                    day_index=day_index,
                    route_index=0,
                    origin=origin_city,
                    destination=destination_city,
                ),
                departure_at=guaranteed_departure_at,
                arrival_at=guaranteed_departure_at + timedelta(minutes=95),
                price_amount=Decimal("3990.00"),
                available_seats=24,
                source_record_id=_source_record_id(
                    prefix="guaranteed-direct",
                    base_date=reference_date,
                    day_index=day_index,
                    route_index=0,
                    origin=origin_city,
                    destination=destination_city,
                    departure_at=guaranteed_departure_at,
                ),
                valid_from=reference_date - timedelta(days=30),
            )
        )

        # Guarantee at least one 1-transfer route MOW -> X -> SPB per day
        try:
            transfer_choice = None
            for loc in transfer_pool:
                if loc.id not in {origin_city.id, destination_city.id}:
                    transfer_choice = loc
                    break
            if transfer_choice is not None:
                transfer_location = transfer_choice
                first_departure_at = _build_departure_at(
                    travel_date=travel_date,
                    day_index=day_index,
                    route_index=0,
                    window_minutes=TRANSFER_DEPARTURE_WINDOW_MINUTES,
                    anchor_hour=6,
                    salt=83,
                )
                first_duration_minutes = _build_transfer_leg_duration_minutes(
                    rng,
                    transport_type=TRANSPORT_TYPE_ORDER[(day_index + 1) % len(TRANSPORT_TYPE_ORDER)],
                    day_index=day_index,
                    route_index=0,
                    leg_index=1,
                )
                first_arrival_at = first_departure_at + timedelta(minutes=first_duration_minutes)
                layover_minutes = max(MIN_TRANSFER_LAYOVER_MINUTES, _build_layover_minutes(day_index=day_index, route_index=0))
                second_departure_at = first_arrival_at + timedelta(minutes=layover_minutes)
                second_duration_minutes = _build_transfer_leg_duration_minutes(
                    rng,
                    transport_type=TRANSPORT_TYPE_ORDER[(day_index + 2) % len(TRANSPORT_TYPE_ORDER)],
                    day_index=day_index,
                    route_index=0,
                    leg_index=2,
                )
                second_arrival_at = second_departure_at + timedelta(minutes=second_duration_minutes)
                chain_token = (
                    f"{origin_city.code or str(origin_city.id)[:8]}->"
                    f"{transfer_location.code or str(transfer_location.id)[:8]}->"
                    f"{destination_city.code or str(destination_city.id)[:8]}"
                )
                route_segments.append(
                    _build_route_segment(
                        key=(
                            "guaranteed-transfer",
                            reference_date,
                            day_index,
                            "leg1",
                            origin_city.code,
                            transfer_location.code,
                        ),
                        origin=origin_city,
                        destination=transfer_location,
                        carrier=_pick_carrier(
                            carriers_by_transport_type,
                            transport_type=TRANSPORT_TYPE_ORDER[(day_index + 1) % len(TRANSPORT_TYPE_ORDER)],
                            day_index=day_index,
                            route_index=0,
                        ),
                        transport_type=TRANSPORT_TYPE_ORDER[(day_index + 1) % len(TRANSPORT_TYPE_ORDER)],
                        segment_code=_segment_code(
                            prefix="GTR1",
                            day_index=day_index,
                            route_index=0,
                            origin=origin_city,
                            destination=transfer_location,
                        ),
                        departure_at=first_departure_at,
                        arrival_at=first_arrival_at,
                        price_amount=_build_price_amount(
                            transport_type=TRANSPORT_TYPE_ORDER[(day_index + 1) % len(TRANSPORT_TYPE_ORDER)],
                            day_index=day_index,
                            route_index=0,
                            transfer_leg=True,
                        ),
                        available_seats=_build_available_seats(
                            transport_type=TRANSPORT_TYPE_ORDER[(day_index + 1) % len(TRANSPORT_TYPE_ORDER)],
                            route_index=0,
                            transfer_leg=True,
                        ),
                        source_record_id=_source_record_id(
                            prefix="guaranteed-transfer",
                            base_date=reference_date,
                            day_index=day_index,
                            route_index=0,
                            origin=origin_city,
                            destination=transfer_location,
                            departure_at=first_departure_at,
                            chain_token=chain_token,
                            extra="leg1",
                        ),
                        valid_from=reference_date - timedelta(days=30),
                    )
                )
                route_segments.append(
                    _build_route_segment(
                        key=(
                            "guaranteed-transfer",
                            reference_date,
                            day_index,
                            "leg2",
                            transfer_location.code,
                            destination_city.code,
                        ),
                        origin=transfer_location,
                        destination=destination_city,
                        carrier=_pick_carrier(
                            carriers_by_transport_type,
                            transport_type=TRANSPORT_TYPE_ORDER[(day_index + 2) % len(TRANSPORT_TYPE_ORDER)],
                            day_index=day_index,
                            route_index=0,
                        ),
                        transport_type=TRANSPORT_TYPE_ORDER[(day_index + 2) % len(TRANSPORT_TYPE_ORDER)],
                        segment_code=_segment_code(
                            prefix="GTR2",
                            day_index=day_index,
                            route_index=0,
                            origin=transfer_location,
                            destination=destination_city,
                        ),
                        departure_at=second_departure_at,
                        arrival_at=second_arrival_at,
                        price_amount=_build_price_amount(
                            transport_type=TRANSPORT_TYPE_ORDER[(day_index + 2) % len(TRANSPORT_TYPE_ORDER)],
                            day_index=day_index,
                            route_index=0,
                            transfer_leg=True,
                        ),
                        available_seats=_build_available_seats(
                            transport_type=TRANSPORT_TYPE_ORDER[(day_index + 2) % len(TRANSPORT_TYPE_ORDER)],
                            route_index=0,
                            transfer_leg=True,
                        ),
                        source_record_id=_source_record_id(
                            prefix="guaranteed-transfer",
                            base_date=reference_date,
                            day_index=day_index,
                            route_index=0,
                            origin=transfer_location,
                            destination=destination_city,
                            departure_at=second_departure_at,
                            chain_token=chain_token,
                            extra="leg2",
                        ),
                        valid_from=reference_date - timedelta(days=30),
                    )
                )
        except Exception:
            # best effort: if something goes wrong while guaranteeing transfer chain,
            # continue without failing the whole bundle generation
            pass

        direct_count = direct_day_quotas[day_index]
        for route_index in range(direct_count):
            origin = _pick_location(
                origin_pool,
                day_index=day_index,
                route_index=route_index,
                salt=11,
            )
            destination = _pick_location(
                destination_pool,
                day_index=day_index,
                route_index=route_index,
                salt=23,
                forbidden_ids={origin.id},
            )
            transport_type = TRANSPORT_TYPE_ORDER[
                (day_index + route_index) % len(TRANSPORT_TYPE_ORDER)
            ]
            carrier = _pick_carrier(
                carriers_by_transport_type,
                transport_type=transport_type,
                day_index=day_index,
                route_index=route_index,
            )
            departure_at = _build_departure_at(
                travel_date=travel_date,
                day_index=day_index,
                route_index=route_index,
                window_minutes=DIRECT_DEPARTURE_WINDOW_MINUTES,
                anchor_hour=5,
                salt=31,
            )
            duration_minutes = _build_direct_duration_minutes(
                rng,
                transport_type=transport_type,
                day_index=day_index,
                route_index=route_index,
            )
            arrival_at = departure_at + timedelta(minutes=duration_minutes)
            route_segments.append(
                _build_route_segment(
                    key=(
                        "direct",
                        reference_date,
                        day_index,
                        route_index,
                        origin.code,
                        destination.code,
                        departure_at,
                    ),
                    origin=origin,
                    destination=destination,
                    carrier=carrier,
                    transport_type=transport_type,
                    segment_code=_segment_code(
                        prefix="DRT",
                        day_index=day_index,
                        route_index=route_index,
                        origin=origin,
                        destination=destination,
                    ),
                    departure_at=departure_at,
                    arrival_at=arrival_at,
                    price_amount=_build_price_amount(
                        transport_type=transport_type,
                        day_index=day_index,
                        route_index=route_index,
                        transfer_leg=False,
                    ),
                    available_seats=_build_available_seats(
                        transport_type=transport_type,
                        route_index=route_index,
                        transfer_leg=False,
                    ),
                    source_record_id=_source_record_id(
                        prefix="direct",
                        base_date=reference_date,
                        day_index=day_index,
                        route_index=route_index,
                        origin=origin,
                        destination=destination,
                        departure_at=departure_at,
                    ),
                    valid_from=reference_date - timedelta(days=30),
                )
            )

        transfer_route_count = transfer_day_quotas[day_index]
        for route_index in range(transfer_route_count):
            origin = _pick_location(
                origin_pool,
                day_index=day_index,
                route_index=route_index,
                salt=41,
            )
            transfer_location = _pick_location(
                transfer_pool,
                day_index=day_index,
                route_index=route_index,
                salt=53,
                forbidden_ids={origin.id},
            )
            destination = _pick_location(
                destination_pool,
                day_index=day_index,
                route_index=route_index,
                salt=67,
                forbidden_ids={origin.id, transfer_location.id},
            )
            first_transport_type = TRANSPORT_TYPE_ORDER[
                (day_index + route_index + 1) % len(TRANSPORT_TYPE_ORDER)
            ]
            second_transport_type = TRANSPORT_TYPE_ORDER[
                (day_index + route_index + 2) % len(TRANSPORT_TYPE_ORDER)
            ]
            first_carrier = _pick_carrier(
                carriers_by_transport_type,
                transport_type=first_transport_type,
                day_index=day_index,
                route_index=route_index,
            )
            second_carrier = _pick_carrier(
                carriers_by_transport_type,
                transport_type=second_transport_type,
                day_index=day_index,
                route_index=route_index,
            )
            first_departure_at = _build_departure_at(
                travel_date=travel_date,
                day_index=day_index,
                route_index=route_index,
                window_minutes=TRANSFER_DEPARTURE_WINDOW_MINUTES,
                anchor_hour=4,
                salt=79,
            )
            first_duration_minutes = _build_transfer_leg_duration_minutes(
                rng,
                transport_type=first_transport_type,
                day_index=day_index,
                route_index=route_index,
                leg_index=1,
            )
            first_arrival_at = first_departure_at + timedelta(
                minutes=first_duration_minutes,
            )
            layover_minutes = _build_layover_minutes(
                day_index=day_index,
                route_index=route_index,
            )
            second_departure_at = first_arrival_at + timedelta(minutes=layover_minutes)
            second_duration_minutes = _build_transfer_leg_duration_minutes(
                rng,
                transport_type=second_transport_type,
                day_index=day_index,
                route_index=route_index,
                leg_index=2,
            )
            second_arrival_at = second_departure_at + timedelta(
                minutes=second_duration_minutes,
            )
            chain_token = (
                f"{origin.code or str(origin.id)[:8]}->"
                f"{transfer_location.code or str(transfer_location.id)[:8]}->"
                f"{destination.code or str(destination.id)[:8]}"
            )
            chain_key = (
                "transfer",
                reference_date,
                day_index,
                route_index,
            )
            route_segments.append(
                _build_route_segment(
                    key=(*chain_key, "leg1"),
                    origin=origin,
                    destination=transfer_location,
                    carrier=first_carrier,
                    transport_type=first_transport_type,
                    segment_code=_segment_code(
                        prefix="TRF1",
                        day_index=day_index,
                        route_index=route_index,
                        origin=origin,
                        destination=transfer_location,
                    ),
                    departure_at=first_departure_at,
                    arrival_at=first_arrival_at,
                    price_amount=_build_price_amount(
                        transport_type=first_transport_type,
                        day_index=day_index,
                        route_index=route_index,
                        transfer_leg=True,
                    ),
                    available_seats=_build_available_seats(
                        transport_type=first_transport_type,
                        route_index=route_index,
                        transfer_leg=True,
                    ),
                    source_record_id=_source_record_id(
                        prefix="transfer",
                        base_date=reference_date,
                        day_index=day_index,
                        route_index=route_index,
                        origin=origin,
                        destination=transfer_location,
                        departure_at=None,
                        chain_token=chain_token,
                        extra="leg1",
                    ),
                    valid_from=reference_date - timedelta(days=30),
                )
            )
            route_segments.append(
                _build_route_segment(
                    key=(*chain_key, "leg2"),
                    origin=transfer_location,
                    destination=destination,
                    carrier=second_carrier,
                    transport_type=second_transport_type,
                    segment_code=_segment_code(
                        prefix="TRF2",
                        day_index=day_index,
                        route_index=route_index,
                        origin=transfer_location,
                        destination=destination,
                    ),
                    departure_at=second_departure_at,
                    arrival_at=second_arrival_at,
                    price_amount=_build_price_amount(
                        transport_type=second_transport_type,
                        day_index=day_index,
                        route_index=route_index,
                        transfer_leg=True,
                    ),
                    available_seats=_build_available_seats(
                        transport_type=second_transport_type,
                        route_index=route_index,
                        transfer_leg=True,
                    ),
                    source_record_id=_source_record_id(
                        prefix="transfer",
                        base_date=reference_date,
                        day_index=day_index,
                        route_index=route_index,
                        origin=transfer_location,
                        destination=destination,
                        departure_at=None,
                        chain_token=chain_token,
                        extra="leg2",
                    ),
                    valid_from=reference_date - timedelta(days=30),
                )
            )

    transfer_segments = transfer_routes_target * 2
    return LoadTestBundle(
        route_segments=tuple(route_segments),
        base_date=reference_date,
        days=days,
        target_segments=target_segments,
        direct_segments=direct_segments_target + guaranteed_direct_segments,
        transfer_segments=transfer_segments,
        transfer_routes=transfer_routes_target,
    )


async def _load_seed_inputs(
    session: AsyncSession,
) -> tuple[tuple[Location, ...], tuple[Carrier, ...]]:
    locations_result = await session.execute(select(Location).order_by(Location.name))
    carriers_result = await session.execute(
        select(Carrier).where(Carrier.is_active.is_(True)).order_by(Carrier.name)
    )
    locations = tuple(locations_result.scalars().all())
    carriers = tuple(carriers_result.scalars().all())
    return locations, carriers


async def _insert_route_segments(
    session: AsyncSession,
    route_segments: Sequence[RouteSegment],
    *,
    batch_size: int,
) -> None:
    if batch_size <= 0:
        raise ValueError("batch_size must be positive")

    for chunk in _chunked(route_segments, batch_size):
        session.add_all(chunk)
        await session.flush()


def _select_usable_locations(locations: Sequence[Location]) -> tuple[Location, ...]:
    usable_locations = [location for location in locations if location.country_code == "RU"]
    if not usable_locations:
        usable_locations = list(locations)
    return tuple(usable_locations)


def _find_location_by_code(
    locations: Sequence[Location],
    code: str,
) -> Location:
    for location in locations:
        if location.code == code:
            return location
    raise ValueError(f"Location with code {code} was not found")


def _build_carriers_by_transport_type(
    carriers: Sequence[Carrier],
) -> dict[TransportType, tuple[Carrier, ...]]:
    carriers_by_transport_type: defaultdict[TransportType, list[Carrier]] = defaultdict(list)
    for carrier in carriers:
        if carrier.is_active:
            carriers_by_transport_type[carrier.transport_type].append(carrier)

    return {
        transport_type: tuple(values)
        for transport_type, values in carriers_by_transport_type.items()
        if values
    }


def _split_evenly(total: int, parts: int) -> tuple[int, ...]:
    base_value, remainder = divmod(total, parts)
    return tuple(
        base_value + (1 if index < remainder else 0) for index in range(parts)
    )


def _pick_location(
    locations: Sequence[Location],
    *,
    day_index: int,
    route_index: int,
    salt: int,
    forbidden_ids: set[object] | None = None,
) -> Location:
    if not locations:
        raise ValueError("Location pool is empty")

    blocked_ids = set() if forbidden_ids is None else forbidden_ids
    index = abs(day_index * 31 + route_index * 17 + salt) % len(locations)
    for step in range(len(locations)):
        candidate = locations[(index + step) % len(locations)]
        if candidate.id not in blocked_ids:
            return candidate
    raise ValueError("Unable to pick a distinct location")


def _pick_carrier(
    carriers_by_transport_type: dict[TransportType, tuple[Carrier, ...]],
    *,
    transport_type: TransportType,
    day_index: int,
    route_index: int,
) -> Carrier:
    carriers = carriers_by_transport_type.get(transport_type)
    if carriers:
        index = abs(day_index * 23 + route_index * 19) % len(carriers)
        return carriers[index]

    fallback_carriers = tuple(
        carrier for values in carriers_by_transport_type.values() for carrier in values
    )
    if not fallback_carriers:
        raise ValueError("No carriers available")
    index = abs(day_index * 23 + route_index * 19) % len(fallback_carriers)
    return fallback_carriers[index]


def _build_departure_at(
    *,
    travel_date: date,
    day_index: int,
    route_index: int,
    window_minutes: int,
    anchor_hour: int,
    salt: int,
) -> datetime:
    offset_minutes = abs(day_index * 97 + route_index * 29 + salt) % window_minutes
    return datetime.combine(
        travel_date,
        time(hour=anchor_hour, minute=0),
    ) + timedelta(minutes=offset_minutes)


def _build_direct_duration_minutes(
    rng: Random,
    *,
    transport_type: TransportType,
    day_index: int,
    route_index: int,
) -> int:
    base_durations = {
        TransportType.plane: 90,
        TransportType.train: 150,
        TransportType.bus: 180,
    }
    duration_spans = {
        TransportType.plane: 180,
        TransportType.train: 240,
        TransportType.bus: 300,
    }
    base_duration = base_durations[transport_type]
    duration_span = duration_spans[transport_type]
    return base_duration + rng.randint(0, duration_span) + (day_index + route_index) % 45


def _build_transfer_leg_duration_minutes(
    rng: Random,
    *,
    transport_type: TransportType,
    day_index: int,
    route_index: int,
    leg_index: int,
) -> int:
    base_durations = {
        TransportType.plane: 75,
        TransportType.train: 120,
        TransportType.bus: 150,
    }
    duration_spans = {
        TransportType.plane: 150,
        TransportType.train: 180,
        TransportType.bus: 240,
    }
    base_duration = base_durations[transport_type]
    duration_span = duration_spans[transport_type]
    return (
        base_duration
        + rng.randint(0, duration_span)
        + (day_index + route_index + leg_index) % 30
    )


def _build_layover_minutes(*, day_index: int, route_index: int) -> int:
    layover_candidates = (60, 90, 120, 180, 240, 360, 480, 720)
    index = abs(day_index * 13 + route_index * 7) % len(layover_candidates)
    return layover_candidates[index]


def _build_price_amount(
    *,
    transport_type: TransportType,
    day_index: int,
    route_index: int,
    transfer_leg: bool,
) -> Decimal:
    base_prices = {
        TransportType.plane: Decimal("4200.00"),
        TransportType.train: Decimal("1800.00"),
        TransportType.bus: Decimal("900.00"),
    }
    price_spans = {
        TransportType.plane: Decimal("7600.00"),
        TransportType.train: Decimal("3600.00"),
        TransportType.bus: Decimal("1800.00"),
    }
    transfer_multiplier = Decimal("0.88") if transfer_leg else Decimal("1.0")
    variation = Decimal((day_index * 41 + route_index * 17) % 97) / Decimal("100")
    price = (
        base_prices[transport_type]
        + price_spans[transport_type] * variation
    ) * transfer_multiplier
    return price.quantize(RUB_QUANTIZER, rounding=ROUND_HALF_UP)


def _build_available_seats(
    *,
    transport_type: TransportType,
    route_index: int,
    transfer_leg: bool,
) -> int:
    seat_bases = {
        TransportType.plane: 80,
        TransportType.train: 240,
        TransportType.bus: 45,
    }
    seat_spans = {
        TransportType.plane: 18,
        TransportType.train: 40,
        TransportType.bus: 20,
    }
    base_seats = seat_bases[transport_type]
    span = seat_spans[transport_type]
    transfer_penalty = 8 if transfer_leg else 0
    return max(1, base_seats - transfer_penalty - (route_index % span))


def _build_route_segment(
    *,
    key: Iterable[object],
    origin: Location,
    destination: Location,
    carrier: Carrier,
    transport_type: TransportType,
    segment_code: str,
    departure_at: datetime,
    arrival_at: datetime,
    price_amount: Decimal,
    available_seats: int,
    source_record_id: str,
    valid_from: datetime,
) -> RouteSegment:
    return RouteSegment(
        id=stable_uuid("|".join(str(part) for part in key)),
        origin_location_id=origin.id,
        destination_location_id=destination.id,
        carrier_id=carrier.id,
        transport_type=transport_type,
        segment_code=segment_code,
        departure_at=departure_at,
        arrival_at=arrival_at,
        duration_minutes=0,
        price_amount=price_amount,
        currency_code="RUB",
        available_seats=available_seats,
        source_system=SOURCE_SYSTEM_NAME,
        source_record_id=source_record_id,
        is_active=True,
        valid_from=valid_from,
        valid_to=None,
    )


def _segment_code(
    *,
    prefix: str,
    day_index: int,
    route_index: int,
    origin: Location,
    destination: Location,
) -> str:
    origin_code = origin.code or str(origin.id)[:8]
    destination_code = destination.code or str(destination.id)[:8]
    return (
        f"{prefix}-{day_index:02d}-{route_index:05d}-"
        f"{origin_code}-{destination_code}"
    )


def _source_record_id(
    *,
    prefix: str,
    base_date: date,
    day_index: int,
    route_index: int,
    origin: Location,
    destination: Location,
    departure_at: datetime | None,
    chain_token: str | None = None,
    extra: str | None = None,
) -> str:
    origin_code = origin.code or str(origin.id)[:8]
    destination_code = destination.code or str(destination.id)[:8]
    parts = [
        SOURCE_SYSTEM_NAME,
        prefix,
        base_date.isoformat(),
        f"day-{day_index:02d}",
        f"route-{route_index:05d}",
    ]
    if chain_token is None:
        parts.extend((origin_code, destination_code))
    else:
        parts.append(chain_token)
    if departure_at is not None:
        parts.append(departure_at.isoformat())
    if extra is not None:
        parts.append(extra)
    return ":".join(parts)


def _chunked(
    values: Sequence[RouteSegment],
    batch_size: int,
) -> Iterable[tuple[RouteSegment, ...]]:
    for start in range(0, len(values), batch_size):
        yield tuple(values[start : start + batch_size])


__all__ = [
    "DEFAULT_LOAD_TEST_BASE_DATE",
    "DEFAULT_LOAD_TEST_BATCH_SIZE",
    "DEFAULT_LOAD_TEST_DAYS",
    "DEFAULT_LOAD_TEST_RANDOM_SEED",
    "DEFAULT_LOAD_TEST_TARGET_SEGMENTS",
    "LoadTestBundle",
    "LoadTestStats",
    "build_load_test_data_bundle",
    "seed_load_test_data",
]