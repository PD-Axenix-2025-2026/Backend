#!/usr/bin/env python3
"""
Import RZD locations using station names from railway_stations.xls and/or
city names from a normalized Yandex JSON file.

Workflow:
1) Load station names from JSON file
2) Load city names from a normalized JSON file (keys or normalized_name).
3) Normalize station names (keep only "число км" if present).
4) Query RZD autocomplete for each name (concurrent).
5) Save unique results to JSON, optionally merge into existing JSON, and import to DB.
"""

import argparse
import asyncio
import json
import logging
import os
import re
import sys
from datetime import datetime
from typing import Any, cast

import httpx

from app.core.config import get_settings
from app.core.database import build_engine, build_session_factory, dispose_engine
from app.scripts.import_rzd_locations import RZDLocationImporter

logger = logging.getLogger(__name__)


def normalize_station_name(value: Any) -> str:
    """Normalize station name; keep only '<число> км' if present."""
    if value is None:
        return ""
    name = str(value).strip()
    if not name:
        return ""

    match = re.search(r"\b(\d+)\s*км\.?\b", name.lower())
    if match:
        return f"{match.group(1)} км"

    return name


def load_station_names_from_json(
    file_path: str,
    key: str | None = None,
) -> list[str]:
    """Load station names from a JSON file.

    Supports:
    - JSON array of strings: ["Москва", "Питер", ...]
    - JSON array of objects with key: [{"name": "Москва"}, ...]
    """
    with open(file_path, encoding="utf-8") as file:
        data = json.load(file)

    names: list[str] = []
    seen: set[str] = set()

    if isinstance(data, list):
        for item in data:
            if isinstance(item, str):
                name = item.strip()
            elif isinstance(item, dict) and key:
                name = str(item.get(key, "")).strip()
            else:
                continue

            if name and name not in seen:
                seen.add(name)
                names.append(name)

    return names


def load_city_names_from_json(file_path: str) -> list[str]:
    """Load city names from a normalized JSON file.

    Supports either:
    - dict: {normalized_name: {...}}
    - list: [{"normalized_name": "..."}, ...] or ["name", ...]
    """
    with open(file_path, encoding="utf-8") as file:
        data = json.load(file)

    names: list[str] = []
    seen: set[str] = set()

    if isinstance(data, dict):
        iterable = data.keys()
        for name in iterable:
            name = str(name).strip()
            if name and name not in seen:
                seen.add(name)
                names.append(name)
        return names

    if isinstance(data, list):
        for item in data:
            name = ""
            if isinstance(item, dict):
                name = str(
                    item.get("normalized_name") or item.get("name") or ""
                ).strip()
            elif isinstance(item, str):
                name = item.strip()

            if name and name not in seen:
                seen.add(name)
                names.append(name)

        return names

    raise ValueError("Unsupported JSON format for city names.")


def merge_unique_names(*groups: list[str]) -> list[str]:
    merged: list[str] = []
    seen: set[str] = set()

    for group in groups:
        for name in group:
            if name and name not in seen:
                seen.add(name)
                merged.append(name)

    return merged


class RZDSuggestByNameFetcher:
    """Fetch RZD locations using station names as autocomplete queries."""

    def __init__(
        self,
        language: str = "ru",
        max_concurrency: int = 1,
        timeout_seconds: float = 20.0,
        pause_every: int = 200,
        pause_seconds: float = 1.0,
        progress_every: int = 50,
    ) -> None:
        self.language = language
        self.max_concurrency = max_concurrency
        self.timeout_seconds = timeout_seconds
        self.pause_every = pause_every
        self.pause_seconds = pause_seconds
        self.progress_every = progress_every
        self.suggester_url = "https://ticket.rzd.ru/api/v1/suggests"

    @staticmethod
    def _is_valid_rzd_location(item: Any) -> bool:
        return (
            isinstance(item, dict)
            and "name" in item
            and "expressCode" in item
            and item.get("countryIso", "") == "RU"
        )

    async def _fetch_locations_for_name(
        self,
        client: httpx.AsyncClient,
        name: str,
        semaphore: asyncio.Semaphore,
    ) -> list[dict[str, Any]] | None:
        params: dict[str, Any] = {
            "Query": name,
            "language": self.language,
            "SynonymOn": 1,
        }

        try:
            async with semaphore:
                response = await client.get(
                    self.suggester_url,
                    params=params,
                    headers={
                        "Accept": "application/json",
                        "User-Agent": "Mozilla/5.0",
                    },
                )
            response.raise_for_status()
            data = response.json()
            if not isinstance(data, list):
                return []

            return [item for item in data if self._is_valid_rzd_location(item)]

        except Exception as exc:
            logger.debug("Suggest request failed for '%s': %s", name, exc)
            return None

    async def fetch_locations_for_names(
        self,
        names: list[str],
    ) -> tuple[list[dict[str, Any]], dict[str, int]]:
        stats = {
            "names_total": len(names),
            "requests": 0,
            "raw_candidates": 0,
            "unique_locations": 0,
            "errors": 0,
        }

        if not names:
            return [], stats

        stats["requests"] = len(names)
        results: list[dict[str, Any]] = []

        async with httpx.AsyncClient(timeout=self.timeout_seconds) as client:
            semaphore = asyncio.Semaphore(self.max_concurrency)

            async def fetch_with_name(
                name: str,
            ) -> tuple[str, list[dict[str, Any]] | None]:
                items = await self._fetch_locations_for_name(client, name, semaphore)
                return name, items

            tasks = [asyncio.create_task(fetch_with_name(name)) for name in names]

            processed = 0
            for task in asyncio.as_completed(tasks):
                name, items = await task
                if items is None:
                    stats["errors"] += 1
                    items = []

                results.extend(items)

                processed += 1
                if self.progress_every > 0 and (
                    processed == 1 or processed % self.progress_every == 0
                ):
                    logger.info(
                        "Processed %s/%s station names, collected %s candidates",
                        processed,
                        len(tasks),
                        len(results),
                    )

                if (
                    self.pause_every > 0
                    and self.pause_seconds > 0
                    and processed % self.pause_every == 0
                ):
                    logger.info(
                        "Pausing for %.2fs after %s requests",
                        self.pause_seconds,
                        processed,
                    )
                    await asyncio.sleep(self.pause_seconds)

        stats["raw_candidates"] = len(results)

        unique_by_node_id: dict[str, dict[str, Any]] = {}
        for item in results:
            express_code = item.get("expressCode")
            node_id = item.get("nodeId")
            if not express_code:
                continue

            unique_key = node_id if node_id is not None else f"express:{express_code}"
            if unique_key not in unique_by_node_id:
                unique_by_node_id[unique_key] = item

        unique_locations = list(unique_by_node_id.values())
        stats["unique_locations"] = len(unique_locations)

        return unique_locations, stats


def save_to_json(locations: list[dict[str, Any]], filename: str | None = None) -> str:
    if filename is None:
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        filename = f"rzd_locations_from_station_list_{timestamp}.json"

    with open(filename, "w", encoding="utf-8") as file:
        json.dump(locations, file, ensure_ascii=False, indent=2)

    logger.info("Saved %s locations to %s", len(locations), filename)
    return filename


def load_from_json(filename: str) -> list[dict[str, Any]]:
    with open(filename, encoding="utf-8") as file:
        return cast(list[dict[str, Any]], json.load(file))


def merge_locations_by_node_id(
    base: list[dict[str, Any]],
    extra: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    merged: dict[str, dict[str, Any]] = {}

    for item in base:
        express_code = item.get("expressCode")
        node_id = item.get("nodeId")
        if not express_code:
            continue

        unique_key = node_id if node_id is not None else f"express:{express_code}"
        if unique_key not in merged:
            merged[unique_key] = item

    for item in extra:
        express_code = item.get("expressCode")
        node_id = item.get("nodeId")
        if not express_code:
            continue

        unique_key = node_id if node_id is not None else f"express:{express_code}"
        if unique_key not in merged:
            merged[unique_key] = item

    return list(merged.values())


def main() -> None:
    asyncio.run(_main_async())


async def _main_async() -> None:
    args = _parse_args()

    logging.basicConfig(
        level=logging.INFO if not args.verbose else logging.DEBUG,
        format="%(asctime)s - %(levelname)s - %(message)s",
        handlers=[logging.StreamHandler(sys.stdout)],
    )

    station_names: list[str] = []
    if args.stations_file:
        logger.info("Loading station names from %s", args.stations_file)
        station_names = load_station_names_from_json(
            args.stations_file, args.stations_name_key
        )
        logger.info("Station names extracted: %s", len(station_names))

    city_names: list[str] = []
    if args.cities_file:
        logger.info("Loading city names from %s", args.cities_file)
        try:
            city_names = load_city_names_from_json(args.cities_file)
        except ValueError as exc:
            logger.error("Failed to load city names: %s", exc)
            return
        logger.info("City names extracted: %s", len(city_names))

    if not station_names and not city_names:
        logger.error("No names found (provide --stations-file and/or --cities-file).")
        return

    query_names = merge_unique_names(station_names, city_names)
    logger.info(
        "Total unique query names: %s (stations=%s, cities=%s)",
        len(query_names),
        len(station_names),
        len(city_names),
    )

    fetcher = RZDSuggestByNameFetcher(
        language=args.language,
        max_concurrency=args.max_concurrency,
        timeout_seconds=args.timeout_seconds,
        pause_every=args.pause_every,
        pause_seconds=args.pause_seconds,
        progress_every=args.progress_every,
    )

    logger.info(
        "Fetching RZD suggestions with max concurrency=%s",
        args.max_concurrency,
    )
    rzd_locations, stats = await fetcher.fetch_locations_for_names(query_names)

    logger.info(
        "Suggest results: names=%s requests=%s raw=%s unique=%s errors=%s",
        stats["names_total"],
        stats["requests"],
        stats["raw_candidates"],
        stats["unique_locations"],
        stats["errors"],
    )

    existing_locations: list[dict[str, Any]] = []
    if args.append_to_file:
        if os.path.exists(args.append_to_file):
            existing_locations = load_from_json(args.append_to_file)
            logger.info(
                "Loaded %s existing locations from %s",
                len(existing_locations),
                args.append_to_file,
            )
        else:
            logger.warning(
                "Append file not found, will create: %s", args.append_to_file
            )

    should_save = args.save_json or not args.import_db or args.append_to_file
    if should_save:
        merged_locations = (
            merge_locations_by_node_id(existing_locations, rzd_locations)
            if existing_locations
            else rzd_locations
        )
        output_file = args.output_file or args.append_to_file
        saved_file = save_to_json(merged_locations, output_file)
        logger.info("Locations saved to: %s", saved_file)

    if args.import_db:
        settings = get_settings()
        engine = build_engine(settings)
        session_factory = build_session_factory(engine)
        try:
            logger.info("Starting database import...")
            importer = RZDLocationImporter(language=args.language)
            db_stats = await importer.import_to_database(session_factory, rzd_locations)

            print("\n=== RZD Locations Import Results ===")
            print(f"Total locations found:   {db_stats['total']}")
            print(f"New locations added:     {db_stats['added']}")
            print(f"Already existing:        {db_stats['skipped']}")
            print(f"Errors:                  {db_stats['errors']}")
        finally:
            await dispose_engine(engine)


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Import RZD locations using station names and/or city names.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  # Fetch RZD suggests using stations list and save to JSON
  python app/scripts/import_rzd_locations_using_stations_list.py \
    --stations-file station_names_dataset.json

    # Fetch using normalized city names JSON only
    python app/scripts/import_rzd_locations_using_stations_list.py \
        --cities-file yandex_settlements.json

    # Append new city-based results into existing JSON
    python app/scripts/import_rzd_locations_using_stations_list.py \
        --cities-file yandex_settlements.json --append-to-file rzd_locations.json

  # Fetch and import to database
  python app/scripts/import_rzd_locations_using_stations_list.py \
    --stations-file station_names_dataset.json --import-db

  # Fetch with pauses and more frequent progress output
  python app/scripts/import_rzd_locations_using_stations_list.py \
    --stations-file station_names_dataset.json --pause-every 200 --pause-seconds 2 \
    --progress-every 20
        """,
    )

    parser.add_argument(
        "--stations-file",
        type=str,
        help="Path to station_names_dataset.json",
    )
    parser.add_argument(
        "--stations-name-key",
        type=str,
        default=None,
        help="station name key in stations file (default: None)",
    )
    parser.add_argument(
        "--cities-file",
        type=str,
        help="Path to normalized cities JSON (keys are names)",
    )
    parser.add_argument(
        "--import-db",
        action="store_true",
        help="Import locations to database",
    )
    parser.add_argument(
        "--save-json",
        action="store_true",
        help="Save locations to JSON file",
    )
    parser.add_argument(
        "--output-file",
        type=str,
        help="Output JSON file name \
             (default: rzd_locations_from_station_list_TIMESTAMP.json)",
    )
    parser.add_argument(
        "--append-to-file",
        type=str,
        help="Merge new results into existing JSON file",
    )
    parser.add_argument(
        "--language",
        type=str,
        default="ru",
        help="Language for location names (default: ru)",
    )
    parser.add_argument(
        "--max-concurrency",
        type=int,
        default=1,
        help="Max concurrent requests (default: 1)",
    )
    parser.add_argument(
        "--timeout-seconds",
        type=float,
        default=20.0,
        help="Request timeout in seconds (default: 20.0)",
    )
    parser.add_argument(
        "--pause-every",
        type=int,
        default=200,
        help="Pause after every N requests (default: 200, 0 = disabled)",
    )
    parser.add_argument(
        "--pause-seconds",
        type=float,
        default=1.0,
        help="Pause duration in seconds (default: 1.0)",
    )
    parser.add_argument(
        "--progress-every",
        type=int,
        default=50,
        help="Progress log interval (default: 50, 0 = disabled)",
    )
    parser.add_argument(
        "--verbose",
        action="store_true",
        help="Enable verbose logging",
    )

    return parser.parse_args()


if __name__ == "__main__":
    main()
