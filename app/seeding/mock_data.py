from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from typing import cast

from sqlalchemy import Table, delete
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.models.carrier import Carrier
from app.models.location import Location
from app.models.route_segment import RouteSegment
from app.seeding.builders import build_carriers, build_locations, build_route_segments
from app.seeding.catalog import SEED_DATE_OFFSETS

SEED_TABLES: tuple[Table, ...] = (
    cast(Table, RouteSegment.__table__),
    cast(Table, Carrier.__table__),
    cast(Table, Location.__table__),
)


@dataclass(slots=True, frozen=True)
class SeedStats:
    locations: int
    carriers: int
    route_segments: int
    base_date: date


@dataclass(slots=True, frozen=True)
class MockSeedBundle:
    locations: tuple[Location, ...]
    carriers: tuple[Carrier, ...]
    route_segments: tuple[RouteSegment, ...]
    base_date: date


async def seed_mock_data(
    session_factory: async_sessionmaker[AsyncSession],
    *,
    base_date: date | None = None,
) -> SeedStats:
    bundle = build_mock_data_bundle(base_date)

    async with session_factory() as session:
        await _reset_seed_tables(session)
        session.add_all(bundle.locations)
        session.add_all(bundle.carriers)
        session.add_all(bundle.route_segments)
        await session.commit()

    return SeedStats(
        locations=len(bundle.locations),
        carriers=len(bundle.carriers),
        route_segments=len(bundle.route_segments),
        base_date=bundle.base_date,
    )


def build_mock_data_bundle(base_date: date | None = None) -> MockSeedBundle:
    reference_date = date.today() if base_date is None else base_date
    locations = build_locations()
    carriers = build_carriers()
    route_segments = build_route_segments(
        base_date=reference_date,
        locations=locations,
        carriers=carriers,
    )
    return MockSeedBundle(
        locations=tuple(locations.values()),
        carriers=tuple(carriers.values()),
        route_segments=tuple(route_segments),
        base_date=reference_date,
    )


async def _reset_seed_tables(session: AsyncSession) -> None:
    await session.execute(delete(RouteSegment))
    await session.execute(delete(Carrier))
    await session.execute(delete(Location))


__all__ = [
    "MockSeedBundle",
    "SEED_DATE_OFFSETS",
    "SEED_TABLES",
    "SeedStats",
    "build_mock_data_bundle",
    "seed_mock_data",
]
