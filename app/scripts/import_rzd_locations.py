#!/usr/bin/env python3
"""
Скрипт для импорта локаций (пока только, содержащих expressCode) РЖД в базу данных.
Может работать в трех режимах:
1. Загрузка из API и сохранение в JSON (по умолчанию)
2. Загрузка из API и импорт в БД
3. Импорт в БД из существующего JSON файла
"""

import argparse
import asyncio
import json
import logging
import sys
import uuid
from datetime import datetime
from typing import Any

import httpx
from sqlalchemy import select
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


class RZDLocationImporter:
    """Импортер локаций РЖД"""

    RZD_NAMESPACE = uuid.UUID("a1b2c3d4-e5f6-7890-abcd-ef1234567890")

    def __init__(self, language: str = "ru"):
        self.language = language
        self.suggester_url = "https://ticket.rzd.ru/api/v1/suggests"

    async def fetch_locations(self, prefix: str) -> list[dict[str, Any]]:
        """
        Получение локаций по префиксу из suggester API

        Args:
            prefix: Префикс названия (минимум 2 символа)

        Returns:
            Список локаций
        """
        params = {
            "Query": prefix,
            "language": self.language,
        }

        async with httpx.AsyncClient(timeout=30.0) as client:
            try:
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

                locations = []
                if isinstance(data, list):
                    for item in data:
                        if (
                            isinstance(item, dict)
                            and "name" in item
                            and "expressCode" in item
                        ):
                            locations.append(item)

                return locations

            except Exception as e:
                logger.debug(f"Error fetching locations for prefix '{prefix}': {e}")
                return []

    async def fetch_all_locations(self) -> list[dict[str, Any]]:
        """
        Получение всех локаций через перебор двухбуквенных префиксов

        Returns:
            Список всех уникальных локаций
        """
        alphabet = "абвгдежзийклмнопрстуфхцчшщъыьэюя"
        seen_codes = set()
        all_locations = []

        total_prefixes = len(alphabet) ** 2
        processed = 0

        for char1 in alphabet:
            for char2 in alphabet:
                prefix = char1 + char2
                locations = await self.fetch_locations(prefix)

                # Добавляем только уникальные локации по expressCode
                for location in locations:
                    code = location.get("expressCode")
                    if code and code not in seen_codes:
                        seen_codes.add(code)
                        all_locations.append(location)

                processed += 1
                if processed % 100 == 0:
                    logger.info(
                        f"Processed {processed}/{total_prefixes} prefixes, "
                        f"found {len(all_locations)} unique locations so far"
                    )

                # Небольшая задержка чтобы не перегружать API
                await asyncio.sleep(0.05)

        return all_locations

    def map_location_type(self, location: dict[str, Any]) -> LocationType:
        """
        Определение типа локации на основе данных API

        Args:
            location: Данные локации из API

        Returns:
            LocationType
        """
        transport_type = location.get("transportType", "")
        node_type = location.get("nodeType", "")

        if transport_type == "train" and node_type == "station":
            return LocationType.railway_station
        elif transport_type == "avia":
            return LocationType.airport
        elif transport_type == "bus":
            return LocationType.bus_station
        elif transport_type == "city" and node_type == "city":
            return LocationType.city
        else:
            return LocationType.railway_station  # По умолчанию

    def extract_region_info(self, region_str: str) -> tuple[str, str | None]:
        """
        Извлечение информации о регионе

        Args:
            region_str: Строка с регионом из API

        Returns:
            Кортеж (часовой пояс (пока всегда "Europe/Moscow"!), название города)
        """
        city_name = None

        if region_str:
            parts = region_str.split(",")
            if parts:
                # Первая часть обычно содержит тип и название населенного пункта
                first_part = parts[0].strip()
                if first_part.startswith("город"):
                    city_name = first_part.replace("город", "").strip()
                elif first_part.startswith("г."):
                    city_name = first_part.replace("г.", "").strip()
                else:
                    city_name = first_part

        return "Europe/Moscow", city_name

    async def import_to_database(
        self,
        session_factory: async_sessionmaker[AsyncSession],
        locations: list[dict[str, Any]],
    ) -> dict[str, int]:
        """
        Импорт локаций в базу данных

        Args:
            session_factory: Фабрика сессий SQLAlchemy
            locations: Список локаций для импорта

        Returns:
            Статистика импорта
        """
        stats = {
            "total": len(locations),
            "added": 0,
            "skipped": 0,
            "errors": 0,
        }

        async with session_factory() as session:
            for location_data in locations:
                try:
                    code = str(location_data.get("expressCode"))
                    name = str(location_data.get("name"))

                    if not code or not name:
                        logger.warning(
                            f"Skipping location without code or name: {location_data}"
                        )
                        stats["skipped"] += 1
                        continue

                    # Проверяем существует ли локация
                    result = await session.execute(
                        select(Location).where(Location.code == code)
                    )
                    existing = result.scalar_one_or_none()

                    if existing is None:
                        # Извлекаем информацию
                        timezone, city_name = self.extract_region_info(
                            location_data.get("region", "")
                        )

                        # Создаем новую локацию
                        location = Location(
                            id=uuid.uuid5(self.RZD_NAMESPACE, code),
                            code=code,
                            name=name,
                            city_name=city_name or name,
                            country_code=location_data.get("countryIso", "RU"),
                            location_type=self.map_location_type(location_data),
                            lat=None,
                            lon=None,
                            timezone=timezone,
                            is_hub=False,
                        )
                        session.add(location)
                        stats["added"] += 1

                        logger.debug(f"Added location: {name} ({code})")
                    else:
                        stats["skipped"] += 1

                except Exception as e:
                    logger.error(
                        f"Error importing location {location_data.get('name')} "
                        f"({location_data.get('expressCode')}): {e}"
                    )
                    stats["errors"] += 1

            await session.commit()

        return stats


def save_to_json(locations: list[dict[str, Any]], filename: str | None = None) -> str:
    """
    Сохранение локаций в JSON файл

    Args:
        locations: Список локаций
        filename: Имя файла (если None, генерируется автоматически)

    Returns:
        Имя сохраненного файла
    """
    if filename is None:
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        filename = f"rzd_locations_{timestamp}.json"

    with open(filename, "w", encoding="utf-8") as f:
        json.dump(locations, f, ensure_ascii=False, indent=2)

    logger.info(f"Saved {len(locations)} locations to {filename}")
    return filename


def load_from_json(filename: str) -> list[dict[str, Any]]:
    """
    Загрузка локаций из JSON файла

    Args:
        filename: Имя файла

    Returns:
        Список локаций
    """
    with open(filename, encoding="utf-8") as f:
        locations: list[dict[str, Any]] = json.load(f)

    logger.info(f"Loaded {len(locations)} locations from {filename}")
    return locations


def main() -> None:
    asyncio.run(_main_async())


async def _main_async() -> None:
    args = _parse_args()

    # Настройка логирования
    logging.basicConfig(
        level=logging.INFO if not args.verbose else logging.DEBUG,
        format="%(asctime)s - %(levelname)s - %(message)s",
        handlers=[logging.StreamHandler(sys.stdout)],
    )

    settings = get_settings()
    engine = build_engine(settings)
    session_factory = build_session_factory(engine)

    try:
        importer = RZDLocationImporter(language=args.language)

        # Определяем источник данных
        if args.input_file:
            # Режим 3: Импорт из JSON файла
            logger.info(f"Loading locations from file: {args.input_file}")
            locations = load_from_json(args.input_file)

        else:
            # Режим 1 или 2: Загрузка из API
            logger.info("Starting to fetch locations from RZD API...")
            locations = await importer.fetch_all_locations()

            if not locations:
                logger.error("No locations found from API!")
                return

            logger.info(f"Found {len(locations)} unique locations")

            # Сохраняем в файл если указано или если не нужно в БД
            if args.save_json or not args.import_db:
                filename = args.output_file if args.output_file else None
                saved_file = save_to_json(locations, filename)
                logger.info(f"Locations saved to: {saved_file}")

        # Импорт в БД если нужно
        if args.import_db:
            logger.info("Starting database import...")
            stats = await importer.import_to_database(session_factory, locations)

            # Выводим статистику
            print("\n=== RZD Locations Import Results ===")
            print(f"Total locations found:   {stats['total']}")
            print(f"New locations added:     {stats['added']}")
            print(f"Already existing:        {stats['skipped']}")
            print(f"Errors:                  {stats['errors']}")
        elif not args.save_json and not args.output_file:
            # Если не указано ни сохранение ни импорт, сохраняем по умолчанию
            saved_file = save_to_json(locations)
            logger.info(f"Locations saved to: {saved_file}")

    finally:
        await dispose_engine(engine)


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Import locations (stations/cities) \
                    from RZD API into the database.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  # Fetch from API and save to JSON only
  python app/scripts/import_rzd_locations.py

  # Fetch from API and import to database
  python app/scripts/import_rzd_locations.py --import-db

  # Fetch from API, save to JSON and import to database
  python app/scripts/import_rzd_locations.py --save-json --import-db

  # Import from existing JSON file
  python app/scripts/import_rzd_locations.py --input-file rzd_locations.json --import-db

  # Save to specific file
  python app/scripts/import_rzd_locations.py --save-json --output-file my_locations.json
        """,
    )

    # Группа для источника данных
    source_group = parser.add_mutually_exclusive_group()
    source_group.add_argument(
        "--input-file",
        type=str,
        help="Input JSON file with locations (skip API fetch)",
    )

    # Группа для действий с данными
    action_group = parser.add_argument_group("Actions")
    action_group.add_argument(
        "--import-db",
        action="store_true",
        help="Import locations to database",
    )
    action_group.add_argument(
        "--save-json",
        action="store_true",
        help="Save locations to JSON file",
    )

    # Дополнительные параметры
    parser.add_argument(
        "--output-file",
        type=str,
        help="Output JSON file name (default: rzd_locations_TIMESTAMP.json)",
    )
    parser.add_argument(
        "--language",
        type=str,
        default="ru",
        help="Language for location names (default: ru)",
    )
    parser.add_argument(
        "--verbose",
        action="store_true",
        help="Enable verbose logging",
    )

    return parser.parse_args()


if __name__ == "__main__":
    main()
