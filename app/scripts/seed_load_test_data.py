from __future__ import annotations

import argparse
import asyncio
from datetime import date

from app.core.config import get_settings
from app.core.database import build_engine, build_session_factory, dispose_engine
from app.seeding.load_test_data import (
    DEFAULT_LOAD_TEST_BASE_DATE,
    DEFAULT_LOAD_TEST_BATCH_SIZE,
    DEFAULT_LOAD_TEST_DAYS,
    DEFAULT_LOAD_TEST_RANDOM_SEED,
    DEFAULT_LOAD_TEST_TARGET_SEGMENTS,
    seed_load_test_data,
)


def main() -> None:
    asyncio.run(_main_async())


async def _main_async() -> None:
    args = _parse_args()
    settings = get_settings()
    engine = build_engine(settings)
    session_factory = build_session_factory(engine)

    try:
        stats = await seed_load_test_data(
            session_factory,
            base_date=args.base_date,
            days=args.days,
            target_segments=args.target_segments,
            random_seed=args.random_seed,
            batch_size=args.batch_size,
            append=args.append,
        )
    finally:
        await dispose_engine(engine)

    print(
        "Load-test data seeded successfully: "
        f"{stats.route_segments} route segments "
        f"({stats.direct_segments} direct, "
        f"{stats.transfer_segments} transfer legs, "
        f"{stats.transfer_routes} transfer routes). "
        f"Base date: {stats.base_date.isoformat()}. "
        f"Days: {stats.days}."
    )


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Generate a deterministic load-test dataset of route segments in the "
            "database without recreating reference tables."
        ),
    )
    parser.add_argument(
        "--base-date",
        type=date.fromisoformat,
        default=DEFAULT_LOAD_TEST_BASE_DATE,
        help="Base travel date in YYYY-MM-DD format. Defaults to 2026-05-22.",
    )
    parser.add_argument(
        "--days",
        type=int,
        default=DEFAULT_LOAD_TEST_DAYS,
        help="How many travel days to spread the generated data across.",
    )
    parser.add_argument(
        "--target-segments",
        type=int,
        default=DEFAULT_LOAD_TEST_TARGET_SEGMENTS,
        help="Total number of route_segment rows to generate.",
    )
    parser.add_argument(
        "--random-seed",
        type=int,
        default=DEFAULT_LOAD_TEST_RANDOM_SEED,
        help="Seed for deterministic variation in prices, durations, and schedules.",
    )
    parser.add_argument(
        "--batch-size",
        type=int,
        default=DEFAULT_LOAD_TEST_BATCH_SIZE,
        help="How many rows to flush per batch while inserting.",
    )
    parser.add_argument(
        "--append",
        action="store_true",
        help="Append to existing route_segments instead of replacing them.",
    )
    return parser.parse_args()


if __name__ == "__main__":
    main()