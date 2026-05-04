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

    async def _fetch_locations_with_client(
        self,
        client: httpx.AsyncClient,
        prefix: str,
        semaphore: asyncio.Semaphore | None = None,
    ) -> list[dict[str, Any]]:
        params: dict[str, Any] = {
            "Query": prefix,
            "language": self.language,
            "SynonymOn": 1,
        }

        try:
            if semaphore is None:
                response = await client.get(
                    self.suggester_url,
                    params=params,
                    headers={
                        "Accept": "application/json",
                        "User-Agent": "Mozilla/5.0",
                    },
                )
            else:
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

            locations = []
            if isinstance(data, list):
                for item in data:
                    if (
                        isinstance(item, dict)
                        and "name" in item
                        and "expressCode" in item
                        and item.get("countryIso", "") == "RU"
                    ):
                        locations.append(item)

            return locations

        except Exception as e:
            logger.debug("Error fetching locations for prefix '%s': %s", prefix, e)
            return []

    async def fetch_locations(self, prefix: str) -> list[dict[str, Any]]:
        """
        Получение локаций по префиксу из suggester API

        Args:
            prefix: Префикс названия (минимум 2 символа)

        Returns:
            Список локаций
        """
        async with httpx.AsyncClient(timeout=30.0) as client:
            return await self._fetch_locations_with_client(client, prefix)

    async def fetch_all_locations(
        self,
        max_prefix_length: int | None = None,
        max_concurrency: int = 1,
        pause_every: int = 200,
        pause_seconds: float = 1.0,
        progress_every: int = 50,
    ) -> list[dict[str, Any]]:
        """
        Получение всех локаций через перебор префиксов.

        Сначала перебираем все пары букв, затем углубляемся в тройки и дальше
        только для тех префиксов, где есть непустой ответ. По умолчанию
        глубина не ограничена (остановка по пустым ответам).

        Args:
            max_prefix_length: Максимальная длина префикса (None = без ограничений)
            max_concurrency: Максимальное количество параллельных запросов
            pause_every: Пауза после каждых N запросов (0 = без паузы)
            pause_seconds: Длительность паузы в секундах
            progress_every: Как часто выводить прогресс (0 = отключить)

        Returns:
            Список всех уникальных локаций
        """
        alphabet = "абвгдежзийклмнопрстуфхцчшщъыьэюя"
        seen_node_ids = set()
        all_locations = []
        prefixes = [a + b for a in alphabet for b in alphabet]
        current_length = 2

        async with httpx.AsyncClient(timeout=30.0) as client:
            semaphore = asyncio.Semaphore(max_concurrency)

            async def fetch_with_prefix(
                prefix: str,
            ) -> tuple[str, list[dict[str, Any]]]:
                locations = await self._fetch_locations_with_client(
                    client,
                    prefix,
                    semaphore,
                )
                return prefix, locations

            while prefixes:
                logger.info(
                    "Processing %s prefixes of length %s",
                    len(prefixes),
                    current_length,
                )

                tasks = [
                    asyncio.create_task(fetch_with_prefix(prefix))
                    for prefix in prefixes
                ]

                next_prefixes: set[str] = set()
                processed = 0

                for task in asyncio.as_completed(tasks):
                    prefix, locations = await task

                    if locations:
                        # Добавляем только уникальные локации по nodeId
                        for location in locations:
                            express_code = location.get("expressCode")
                            node_id = location.get("nodeId")
                            if not express_code:
                                continue

                            unique_key = (
                                node_id
                                if node_id is not None
                                else f"express:{express_code}"
                            )
                            if unique_key not in seen_node_ids:
                                seen_node_ids.add(unique_key)
                                all_locations.append(location)

                        if (
                            max_prefix_length is None
                            or current_length < max_prefix_length
                        ):
                            for char in alphabet:
                                next_prefixes.add(prefix + char)

                    processed += 1
                    if progress_every > 0 and (
                        processed == 1 or processed % progress_every == 0
                    ):
                        logger.info(
                            "Processed %s/%s prefixes, \
                            found %s unique locations so far",
                            processed,
                            len(tasks),
                            len(all_locations),
                        )

                    if (
                        pause_every > 0
                        and pause_seconds > 0
                        and processed % pause_every == 0
                    ):
                        logger.info(
                            "Pausing for %.2fs after %s requests",
                            pause_seconds,
                            processed,
                        )
                        await asyncio.sleep(pause_seconds)

                prefixes = sorted(next_prefixes)
                current_length += 1

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
                    code = str(location_data.get("nodeId"))
                    name = str(location_data.get("name"))

                    if not code or not name:
                        logger.warning(
                            f"Skipping location without code or name: {location_data}"
                        )
                        stats["skipped"] += 1
                        continue

                    location_type = self.map_location_type(location_data)

                    country_code = location_data.get("countryIso", "RU")

                    if location_type != LocationType.city:
                        city_id = location_data.get("cityId", "no_id")
                        city_name = next(
                          (loc for loc in locations if loc.get("nodeId") == city_id), {}
                        ).get("name")

                    if not city_name and location_type == LocationType.city:
                        city_name = name

                    location = Location(
                        id=uuid.uuid5(self.RZD_NAMESPACE, code),
                        code=code,
                        name=name,
                        city_name=city_name,
                        country_code=country_code,
                        location_type=location_type,
                        lat=None,
                        lon=None,
                        timezone="Europe/Moscow" if country_code == "RU" else "UTC",
                        is_hub=False,
                    )
                    session.add(location)
                    stats["added"] += 1

                    logger.debug(f"Added location: {name} ({code})")

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
            locations = await importer.fetch_all_locations(
                max_prefix_length=args.max_prefix_length,
                max_concurrency=args.max_concurrency,
                pause_every=args.pause_every,
                pause_seconds=args.pause_seconds,
                progress_every=args.progress_every,
            )

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

  # Fetch with deeper prefix search and higher concurrency
  python app/scripts/import_rzd_locations.py --max-prefix-length 4 --max-concurrency 50
    
  # Fetch with pauses to reduce API load
  python app/scripts/import_rzd_locations.py --pause-every 200 --pause-seconds 2
    
  # Fetch with more frequent progress output
  python app/scripts/import_rzd_locations.py --progress-every 20

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
        "--max-prefix-length",
        type=int,
        default=None,
        help="Max prefix length to explore (default: unlimited)",
    )
    parser.add_argument(
        "--max-concurrency",
        type=int,
        default=1,
        help="Max concurrent requests (default: 25)",
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
