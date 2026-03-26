from __future__ import annotations

import argparse
import asyncio
from datetime import date

from app.core.config import get_settings
from app.core.database import (
    build_engine,
    build_session_factory,
    dispose_engine,
    recreate_models,
)
from app.seeding.mock_data import SEED_DATE_OFFSETS, SEED_TABLES, seed_mock_data


def main() -> None:
    asyncio.run(_main_async())


async def _main_async() -> None:
    args = _parse_args()
    settings = get_settings()
    engine = build_engine(settings)
    session_factory = build_session_factory(engine)

    try:
        await recreate_models(engine, tables=SEED_TABLES)
        stats = await seed_mock_data(
            session_factory,
            base_date=args.base_date,
        )
    finally:
        await dispose_engine(engine)

    offsets = ", ".join(str(offset) for offset in SEED_DATE_OFFSETS)
    print(
        "Mock data seeded successfully: "
        f"{stats.locations} locations, "
        f"{stats.carriers} carriers, "
        f"{stats.route_segments} route segments. "
        "Seed tables schema recreated. "
        f"Base date: {stats.base_date.isoformat()}. "
        f"Available day offsets: {offsets}."
    )


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Recreate seed tables and populate them with deterministic mock route data."
        ),
    )
    parser.add_argument(
        "--base-date",
        type=date.fromisoformat,
        default=None,
        help="Base travel date in YYYY-MM-DD format. Defaults to today's date.",
    )
    return parser.parse_args()


if __name__ == "__main__":
    main()
