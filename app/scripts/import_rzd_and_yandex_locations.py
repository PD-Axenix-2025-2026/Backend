#!/usr/bin/env python3
"""
Скрипт для мержа локаций из РЖД и Яндекс.Расписаний с использованием маппинга.
Объединяет данные из двух источников, разрешает дубликаты и сохраняет в БД.
"""

import argparse
import asyncio
import json
import logging
import sys
import uuid
from typing import Any, cast

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.core.config import get_settings
from app.core.database import (
    build_engine,
    build_session_factory,
    dispose_engine,
)
from app.models.enums import LocationType
from app.models.location import Location

logger = logging.getLogger(__name__)

# Namespace для генерации детерминированных UUID (общий для всех)
LOCATION_NAMESPACE = uuid.UUID("a1b2c3d4-e5f6-7890-abcd-ef1234567890")


class LocationMerger:
    """Класс для мержа локаций из разных источников."""

    def __init__(self) -> None:
        self.stats = {
            "total_rzd": 0,
            "total_yandex": 0,
            "matched": 0,
            "unmatched_rzd": 0,
            "unmatched_yandex": 0,
            "merged": 0,
            "errors": 0,
        }

    def load_rzd_locations(self, filepath: str) -> dict[str, dict[str, Any]]:
        """
        Загрузка РЖД локаций из JSON.
        Группировка по expressCode для быстрого поиска.

        Returns:
            Словарь {(expressCode + nodeType): location_data}
        """
        with open(filepath, encoding="utf-8") as f:
            locations = json.load(f)

        by_code = {}
        by_id = {}
        for loc in locations:
            # "expressCode" + city, либо "expressCode" + station
            code = str(loc.get("expressCode") + loc.get("nodeType"))

            if code:
                by_code[code] = {
                    "express_code": loc.get("expressCode"),
                    "node_id": loc.get("nodeId"),
                    "name": loc.get("name"),
                    "city": loc.get("name") if loc.get("nodeType") == "city" else None,
                    "country_iso": loc.get("countryIso", "RU"),
                    "transport_type": loc.get("transportType"),
                    "node_type": loc.get("nodeType"),
                    "city_id": loc.get("cityId"),
                    "source": "rzd",
                }
                by_id[loc.get("nodeId")] = by_code[code]
            else:
                raise Exception(f"Could not get code for location {loc}")

        for loc in locations:
            if loc["nodeType"] != "city":
                by_id[loc["nodeId"]]["city"] = by_id.get(loc.get("cityId", ""), {}).get(
                    "name"
                )

        self.stats["total_rzd"] = len(by_code)
        logger.info(f"Loaded {len(by_code)} RZD locations from {filepath}")
        return by_code

    def load_yandex_locations(self, filepath: str) -> dict[str, dict[str, Any]]:
        """
        Загрузка Яндекс локаций из JSON.
        Группировка по yandex_code для быстрого поиска.

        Returns:
            Словарь {yandex_code: location_data}
        """
        with open(filepath, encoding="utf-8") as f:
            locations = json.load(f)

        by_code = {}
        for loc in locations:
            yandex_code = str(loc.get("yandex_code"))
            if yandex_code:
                by_code[yandex_code] = {
                    "yandex_code": yandex_code,
                    "name": loc.get("name"),
                    "city": loc.get("city"),
                    "region": loc.get("region"),
                    "country": loc.get("country"),
                    "station_type": loc.get("station_type"),
                    "transport_type": loc.get("transport_type"),
                    "lat": loc.get("lat"),
                    "lon": loc.get("lon"),
                    "full_address": loc.get("full_address"),
                    "source": "yandex",
                }
            else:
                raise Exception(f"Could not get code for location {loc}")

        self.stats["total_yandex"] = len(by_code)
        logger.info(f"Loaded {len(by_code)} Yandex locations from {filepath}")
        return by_code

    def load_matches(self, filepath: str) -> list[dict[str, Any]]:
        """
        Загрузка маппинга между РЖД и Яндекс кодами.

        Ожидаемая структура:
        [
            {
                "expressCode": "2000000",
                "yandex_code": "213",
                ...
            },
            ...
        ]
        """
        with open(filepath, encoding="utf-8") as f:
            matches = cast(list[dict[str, Any]], json.load(f))

        logger.info(f"Loaded {len(matches)} matches from {filepath}")
        return matches

    def get_location_type_from_rzd(
        self,
        transport_type: str,
        node_type: str,
    ) -> LocationType:
        """
        Определение LocationType на основе данных РЖД
        """

        if node_type == "city" and transport_type == "city":
            return LocationType.city

        if transport_type == "train" and node_type == "station":
            return LocationType.railway_station
        elif transport_type == "avia":
            return LocationType.airport
        elif transport_type == "bus":
            return LocationType.bus_station
        else:
            return LocationType.railway_station

    def get_location_type_from_yandex(
        self,
        station_type: str,
        transport_type: str,
    ) -> LocationType:
        """Определение LocationType на основе данных Яндекс."""
        if transport_type == "train" or transport_type == "suburban":
            return LocationType.railway_station
        elif transport_type == "plane":
            return LocationType.airport
        elif transport_type == "bus":
            return LocationType.bus_station
        elif transport_type == "city":
            return LocationType.city

        # fallback
        if station_type in ("train_station", "suburban_station"):
            return LocationType.railway_station
        elif station_type == "airport":
            return LocationType.airport
        elif station_type in ("bus_station", "bus_stop"):
            return LocationType.bus_station
        elif station_type == "city":
            return LocationType.city

        return LocationType.railway_station

    def merge_location(
        self,
        rzd_loc: dict[str, Any] | None,
        yandex_loc: dict[str, Any] | None,
    ) -> dict[str, Any]:
        """
        Объединение данных из двух источников.
        Приоритет: Яндекс данные (более полные) + РЖД code.
        """
        merged: dict[str, Any] = {
            "rzd_code": None,
            "rzd_id": None,
            "yandex_code": None,
            "name": None,
            "city_name": None,
            "country_code": "RU",
            "location_type": None,
            "lat": None,
            "lon": None,
            "timezone": "Europe/Moscow",
            "is_hub": False,
        }

        # Базовые поля из Яндекс (более полные)
        if yandex_loc:
            merged["yandex_code"] = yandex_loc.get("yandex_code")
            merged["name"] = yandex_loc.get("name")
            merged["city_name"] = yandex_loc.get("city")
            merged["lat"] = yandex_loc.get("lat")
            merged["lon"] = yandex_loc.get("lon")
            merged["location_type"] = self.get_location_type_from_yandex(
                yandex_loc.get("station_type", ""),
                yandex_loc.get("transport_type", ""),
            )

        # Дополняем/переопределяем из РЖД
        if rzd_loc:
            merged["rzd_code"] = rzd_loc.get("express_code")
            merged["rzd_id"] = rzd_loc.get("node_id")
            if not merged["name"]:
                merged["name"] = rzd_loc.get("name")
            if not merged["city_name"]:
                merged["city_name"] = rzd_loc.get("city")
            if not merged["location_type"]:
                merged["location_type"] = self.get_location_type_from_rzd(
                    rzd_loc.get("transport_type", ""),
                    rzd_loc.get("node_type", ""),
                )

        # Генерация детерминированного ID на основе yandex_code или rzd_id
        unique_key = merged.get("yandex_code") or merged.get("rzd_id")

        merged["id"] = uuid.uuid5(LOCATION_NAMESPACE, str(unique_key))

        return merged

    async def import_to_database(
        self,
        session_factory: async_sessionmaker[AsyncSession],
        locations: list[dict[str, Any]],
    ) -> dict[str, int]:
        """
        Импорт объединённых локаций в базу данных.
        """
        stats = {
            "total": len(locations),
            "added": 0,
            "skipped": 0,
            "errors": 0,
        }

        async with session_factory() as session:
            for loc in locations:
                try:
                    # Проверяем, существует ли уже локация по rzd_code и yandex_code
                    # existing = None
                    # if loc.get("rzd_code"):
                    #     result = await session.execute(
                    #         select(Location)
                    #         .where(Location.rzd_code == loc["rzd_code"])
                    #     )
                    #     existing = result.scalar_one_or_none()
                    #
                    # if not existing and loc.get("yandex_code"):
                    #     result = await session.execute(
                    #         select(Location)
                    #         .where(Location.yandex_code == loc["yandex_code"])
                    #     )
                    #     existing = result.scalar_one_or_none()

                    # if existing:
                    #     # Обновляем существующую запись недостающими кодами
                    #     update_needed = False
                    #     if loc.get("rzd_code") and not existing.rzd_code:
                    #         existing.rzd_code = loc["rzd_code"]
                    #         update_needed = True
                    #     if loc.get("yandex_code") and not existing.yandex_code:
                    #         existing.yandex_code = loc["yandex_code"]
                    #         update_needed = True
                    #
                    #     if update_needed:
                    #         session.add(existing)
                    #         stats["added"] += 1  # считаем как обновлённую
                    #         logger.debug(f"Updated location: {existing.name}")
                    #     else:
                    #         stats["skipped"] += 1
                    #     continue

                    # Создаём новую локацию
                    location = Location(
                        id=loc["id"],
                        rzd_code=loc.get("rzd_code"),
                        yandex_code=loc.get("yandex_code"),
                        name=loc["name"],
                        city_name=loc.get("city_name"),
                        country_code=loc.get("country_code", "RU"),
                        location_type=loc["location_type"],
                        lat=loc.get("lat"),
                        lon=loc.get("lon"),
                        timezone=loc.get("timezone", "Europe/Moscow"),
                        is_hub=loc.get("is_hub", False),
                    )
                    session.add(location)
                    stats["added"] += 1

                    logger.debug(
                        f"Added location: {location.name} "
                        f"(rzd: {location.rzd_code}, yandex: {location.yandex_code})"
                    )

                except Exception as e:
                    logger.error(f"Error importing location {loc.get('name')}: {e}")
                    stats["errors"] += 1

            await session.commit()

        return stats


async def main_async(args: argparse.Namespace) -> None:
    log_level = logging.DEBUG if args.verbose else logging.INFO
    logging.basicConfig(
        level=log_level,
        format="%(asctime)s - %(levelname)s - %(message)s",
        handlers=[logging.StreamHandler(sys.stdout)],
    )

    merger = LocationMerger()

    logger.info("Loading RZD locations...")
    rzd_locations = merger.load_rzd_locations(args.rzd_file)

    logger.info("Loading Yandex locations...")
    yandex_locations = merger.load_yandex_locations(args.yandex_file)

    logger.info("Loading matches...")
    matches = merger.load_matches(args.matches_file)

    merged_locations: dict[str, dict[str, Any]] = {}  # key = объединённый ID
    processed_rzd = set()
    processed_yandex = set()

    for match in matches:
        rzd_type = "city" if match.get("rzd_type") == "city" else "station"
        rzd_unique_code = str(match.get("expressCode")) + rzd_type
        yandex_code = str(match.get("yandex_code"))

        rzd_loc = rzd_locations.get(rzd_unique_code)
        if not rzd_loc:
            raise Exception(f"rzd code not found in rzd locations: {rzd_unique_code}")

        yandex_loc = yandex_locations.get(yandex_code)

        if not yandex_loc:
            raise Exception(f"yandex code not found in yandex locations: {yandex_code}")

        if not rzd_loc and not yandex_loc:
            logger.warning(
                f"Match references non-existent locations: "
                f"express={rzd_unique_code}, yandex={yandex_code}"
            )
            merger.stats["errors"] += 1
            continue

        merged = merger.merge_location(rzd_loc, yandex_loc)

        # Используем ID как ключ для дедупликации
        key = str(merged["id"])
        if key not in merged_locations:
            merged_locations[key] = merged
            merger.stats["matched"] += 1
        else:
            # Дополняем существующую запись
            existing = merged_locations[key]
            if merged.get("rzd_code") and not existing.get("rzd_code"):
                existing["rzd_code"] = merged["rzd_code"]
            if merged.get("yandex_code") and not existing.get("yandex_code"):
                existing["yandex_code"] = merged["yandex_code"]

        if rzd_loc:
            processed_rzd.add(rzd_unique_code)
        if yandex_loc:
            processed_yandex.add(yandex_code)

    # Добавляем не сопоставленные РЖД локации
    for code, loc in rzd_locations.items():
        if code not in processed_rzd:
            merged = merger.merge_location(loc, None)
            key = str(merged["id"])
            if key not in merged_locations:
                merged_locations[key] = merged
                merger.stats["unmatched_rzd"] += 1

    # Добавляем не сопоставленные Яндекс локации
    for code, loc in yandex_locations.items():
        if code not in processed_yandex:
            merged = merger.merge_location(None, loc)
            key = str(merged["id"])
            if key not in merged_locations:
                merged_locations[key] = merged
                merger.stats["unmatched_yandex"] += 1

    logger.info(f"Merged {len(merged_locations)} unique locations")

    # Сохранение в JSON (опционально)
    if args.output_json:
        output_data = []
        for loc in merged_locations.values():
            loc_copy = loc.copy()
            loc_copy["location_type"] = loc_copy["location_type"].value
            loc_copy["id"] = str(loc_copy["id"])
            output_data.append(loc_copy)

        with open(args.output_json, "w", encoding="utf-8") as f:
            json.dump(output_data, f, ensure_ascii=False, indent=2)
        logger.info(f"Saved merged locations to {args.output_json}")

    # Импорт в БД
    if args.import_db:
        settings = get_settings()
        engine = build_engine(settings)
        session_factory = build_session_factory(engine)

        try:
            logger.info("Starting database import...")
            import_stats = await merger.import_to_database(
                session_factory,
                list(merged_locations.values()),
            )

            print("\n=== Merge Results ===")
            print(f"RZD locations total:        {merger.stats['total_rzd']}")
            print(f"Yandex locations total:     {merger.stats['total_yandex']}")
            print(f"Matched (via mapping):      {merger.stats['matched']}")
            print(f"Unmatched RZD only:         {merger.stats['unmatched_rzd']}")
            print(f"Unmatched Yandex only:      {merger.stats['unmatched_yandex']}")
            print(f"Errors in matching:         {merger.stats['errors']}")
            print("\n=== Database Import Results ===")
            print(f"Total for import:            {import_stats['total']}")
            print(f"Added/Updated:               {import_stats['added']}")
            print(f"Skipped (already complete):  {import_stats['skipped']}")
            print(f"Errors:                      {import_stats['errors']}")

        finally:
            await dispose_engine(engine)
    else:
        print("\n=== Merge Results (no DB import) ===")
        print(f"RZD locations total:        {merger.stats['total_rzd']}")
        print(f"Yandex locations total:     {merger.stats['total_yandex']}")
        print(f"Matched (via mapping):      {merger.stats['matched']}")
        print(f"Unmatched RZD only:         {merger.stats['unmatched_rzd']}")
        print(f"Unmatched Yandex only:      {merger.stats['unmatched_yandex']}")
        print(f"Errors in matching:         {merger.stats['errors']}")
        print(f"Total unique locations:     {len(merged_locations)}")


def parse_args() -> argparse.Namespace:
    """Парсинг аргументов командной строки."""
    parser = argparse.ArgumentParser(
        description="Merge RZD and Yandex locations using mapping file.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  # Just merge and show stats (no DB import)
  python app/scripts/import_rzd_and_yandex_locations.py \\
      --rzd-file rzd_locations.json \\
      --yandex-file yandex_rasp_locations.json \\
      --matches-file rzd_yandex_location_matches.json

  # Merge and import to database
  python app/scripts/import_rzd_and_yandex_locations.py \\
      --rzd-file rzd_locations.json \\
      --yandex-file yandex_rasp_locations.json \\
      --matches-file rzd_yandex_location_matches.json \\
      --import-db

  # Merge, save to JSON and import to database
  python app/scripts/import_rzd_and_yandex_locations.py \\
      --rzd-file rzd_locations.json \\
      --yandex-file yandex_rasp_locations.json \\
      --matches-file rzd_yandex_location_matches.json \\
      --output-json merged_locations.json \\
      --import-db
        """,
    )

    parser.add_argument(
        "--rzd-file",
        type=str,
        required=True,
        help="Path to RZD locations JSON file",
    )
    parser.add_argument(
        "--yandex-file",
        type=str,
        required=True,
        help="Path to Yandex locations JSON file",
    )
    parser.add_argument(
        "--matches-file",
        type=str,
        required=True,
        help="Path to matching file (expressCode -> yandex_code mapping)",
    )
    parser.add_argument(
        "--output-json",
        type=str,
        help="Optional: save merged locations to JSON file",
    )
    parser.add_argument(
        "--import-db",
        action="store_true",
        help="Import merged locations to database",
    )
    parser.add_argument(
        "--verbose",
        action="store_true",
        help="Enable verbose logging",
    )

    return parser.parse_args()


def main() -> None:
    """Точка входа."""
    args = parse_args()
    asyncio.run(main_async(args))


if __name__ == "__main__":
    main()
