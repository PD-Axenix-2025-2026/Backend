#!/usr/bin/env python3
"""
Скрипт для получения всех локаций из API Яндекс.Расписаний и сохранения в JSON/БД.

Режимы работы:
1. Загрузка из API и сохранение в JSON (по умолчанию)
2. Загрузка из API и импорт в БД (--import-db)
3. Загрузка из API, сохранение в JSON и импорт в БД (--save-json --import-db)
4. Импорт в БД из существующего JSON файла (--input-file --import-db)

Требуется API-ключ в .env: PDAXENIX_YANDEX_RASP_API_KEY
Получить ключ: https://yandex.ru/dev/rasp/
"""

import argparse
import asyncio
import json
import logging
import sys
import uuid
from datetime import datetime
from typing import Any, cast

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


class YandexRaspLocationFetcher:
    """Загрузчик локаций из API Яндекс.Расписаний"""

    BASE_URL = "https://api.rasp.yandex-net.ru/v3.0"

    def __init__(self, api_key: str, language: str = "ru_RU"):
        self.api_key = api_key
        self.language = language

    async def fetch_all_stations(self) -> Any:
        """Получение полного списка всех станций (около 40 МБ)."""
        url = f"{self.BASE_URL}/stations_list/"

        async with httpx.AsyncClient(timeout=120.0) as client:
            response = await client.get(
                url,
                params={
                    "apikey": self.api_key,
                    "lang": self.language,
                    "format": "json",
                },
                headers={
                    "Accept": "application/json",
                    "User-Agent": "Mozilla/5.0",
                },
            )
            response.raise_for_status()

            with open("yandex_rasp_raw_reply.json", "w", encoding="utf-8") as f:
                json.dump(response.json(), f, ensure_ascii=False, indent=4)

            return response.json()


class YandexRaspSuggestEnricher:
    """Обогащение городов расширенным адресом через suggests API Яндекс.Расписаний."""

    SUGGEST_URL = "https://suggests.rasp.yandex-net.ru/all_suggests"
    BASE_PARAMS = {
        "client_city": "39",
        "field": "from",
        "format": "old",
        "lang": "ru",
        "national_version": "ru",
        "other_point": "c2",
    }
    BROWSER_HEADERS = {
        "Accept": "*/*",
        "Accept-Encoding": "gzip, deflate, br, zstd",
        "Accept-Language": "ru-RU,ru;q=0.9,en-US;q=0.8,en;q=0.7",
        "Connection": "keep-alive",
        "Origin": "https://rasp.yandex.ru",
        "Referer": "https://rasp.yandex.ru/",
        "Sec-CH-UA": '"Google Chrome";v="147", "Not.A/Brand";v="8", "Chromium";v="147"',
        "Sec-CH-UA-Mobile": "?0",
        "Sec-CH-UA-Platform": '"Windows"',
        "Sec-Fetch-Dest": "empty",
        "Sec-Fetch-Mode": "cors",
        "Sec-Fetch-Site": "cross-site",
        "User-Agent": (
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
            "AppleWebKit/537.36 (KHTML, like Gecko) "
            "Chrome/147.0.0.0 Safari/537.36"
        ),
    }

    def __init__(self, max_concurrency: int = 8, timeout_seconds: float = 20.0):
        self.max_concurrency = max_concurrency
        self.timeout_seconds = timeout_seconds

    @staticmethod
    def _is_city_location(location: dict[str, Any]) -> bool:
        return (
            location.get("station_type") == "city"
            or location.get("transport_type") == "city"
        )

    @staticmethod
    def _extract_suggest_rows(payload: Any) -> list[list[Any]]:
        """Из ответа [null, [[...], ...]] достает массив подсказок."""
        if not isinstance(payload, list) or len(payload) < 2:
            return []

        rows = payload[1]
        if not isinstance(rows, list):
            return []

        return [row for row in rows if isinstance(row, list)]

    @staticmethod
    def _build_address_map(rows: list[list[Any]]) -> dict[str, str]:
        """Формирует словарь code -> полный адрес (3-й элемент массива)."""
        by_code: dict[str, str] = {}
        for row in rows:
            if len(row) < 3:
                continue

            code = row[0]
            full_address = row[2]

            if (
                isinstance(code, str)
                and isinstance(full_address, str)
                and full_address.strip()
            ):
                by_code[code] = full_address.strip()

        return by_code

    async def _fetch_suggest_rows(
        self,
        client: httpx.AsyncClient,
        city_name: str,
        semaphore: asyncio.Semaphore,
    ) -> list[list[Any]] | None:
        params = dict(self.BASE_PARAMS)
        params["part"] = city_name

        try:
            async with semaphore:
                response = await client.get(
                    self.SUGGEST_URL,
                    params=params,
                )
            response.raise_for_status()
            payload = response.json()
            return self._extract_suggest_rows(payload)
        except Exception as exc:
            logger.debug(
                "Suggest request failed for city '%s': %s",
                city_name,
                exc,
            )
            return None

    async def enrich_city_addresses(
        self, locations: list[dict[str, Any]]
    ) -> dict[str, int]:
        """Заполняет full_address только для городов в JSON-данных."""
        city_locations = [
            location
            for location in locations
            if self._is_city_location(location) and location.get("yandex_code")
        ]
        stats = {
            "city_locations": len(city_locations),
            "requests": 0,
            "enriched": 0,
            "missing": 0,
            "errors": 0,
        }

        if not city_locations:
            return stats

        cities_by_name: dict[str, list[dict[str, Any]]] = {}
        for city in city_locations:
            city_name = str(city.get("name") or "").strip()
            if not city_name:
                city["full_address"] = None
                stats["missing"] += 1
                continue

            cities_by_name.setdefault(city_name, []).append(city)

        stats["requests"] = len(cities_by_name)

        async with httpx.AsyncClient(
            timeout=self.timeout_seconds,
            headers=self.BROWSER_HEADERS,
        ) as client:
            semaphore = asyncio.Semaphore(self.max_concurrency)

            async def process_city(
                city_name: str, group: list[dict[str, Any]]
            ) -> dict[str, int]:
                rows = await self._fetch_suggest_rows(client, city_name, semaphore)

                if rows is None:
                    for city in group:
                        city["full_address"] = None
                    return {"enriched": 0, "missing": 0, "errors": len(group)}

                by_code = self._build_address_map(rows)
                result = {"enriched": 0, "missing": 0, "errors": 0}

                for city in group:
                    code = str(city.get("yandex_code") or "")
                    full_address = by_code.get(code)

                    if full_address:
                        city["full_address"] = full_address
                        result["enriched"] += 1
                    else:
                        city["full_address"] = None
                        result["missing"] += 1

                return result

            tasks = [
                asyncio.create_task(process_city(city_name, group))
                for city_name, group in cities_by_name.items()
            ]

            for processed, task in enumerate(asyncio.as_completed(tasks), start=1):
                result = await task
                stats["enriched"] += result["enriched"]
                stats["missing"] += result["missing"]
                stats["errors"] += result["errors"]

                if processed % 200 == 0:
                    logger.info(
                        "Suggest enrichment progress: %s/%s city names processed",
                        processed,
                        len(tasks),
                    )

        return stats


class ResponseParser:
    """Парсер ответа API Яндекс.Расписаний"""

    @staticmethod
    def extract_locations(data: Any) -> list[dict[str, str]]:
        """Извлечение плоского списка всех локаций."""
        locations = []

        # сначала все города
        for country in data.get("countries", []):
            country_title = country.get("title", "")

            if country_title != "Россия":
                continue

            for region in country.get("regions", []):
                region_title = region.get("title", "")
                for settlement in region.get("settlements", []):
                    settlement_title = settlement.get("title", "")

                    # пока что отбрасываем объекты, где нет населенного пункта
                    if (
                        settlement_title == ""
                        or settlement.get("codes").get("yandex_code") is None
                    ):
                        continue

                    locations.append(
                        {
                            "name": settlement_title,
                            "city": settlement_title,
                            "region": region_title,
                            "country": country_title,
                            "station_type": "city",
                            "transport_type": "city",
                            "yandex_code": settlement.get("codes").get("yandex_code"),
                            "lat": None,
                            "lon": None,
                            "full_address": None,
                            "source": "yandex_rasp_api",
                        }
                    )

        # потом все станции
        for country in data.get("countries", []):
            country_title = country.get("title", "")

            if country_title != "Россия":
                continue

            for region in country.get("regions", []):
                region_title = region.get("title", "")
                for settlement in region.get("settlements", []):
                    settlement_title = settlement.get("title", "")
                    for station in settlement.get("stations", []):
                        codes = station.get("codes", {})

                        lat = station.get("latitude")
                        if lat == "":
                            lat = None
                        lon = station.get("longitude")
                        if lon == "":
                            lon = None

                        locations.append(
                            {
                                "name": station.get("title", ""),
                                "city": settlement_title,
                                "region": region_title,
                                "country": country_title,
                                "station_type": station.get("station_type"),
                                "transport_type": station.get("transport_type"),
                                "yandex_code": codes.get("yandex_code"),
                                "esr_code": codes.get("esr_code"),
                                "lat": lat,
                                "lon": lon,
                                "source": "yandex_rasp_api",
                            }
                        )

        codes = set()
        unique = list()

        for loc in locations:
            if loc["yandex_code"] in codes:
                logger.debug(f"Found duplicate yandex code for location: {loc}")
                continue
            else:
                codes.add(loc["yandex_code"])
                unique.append(loc)

        return unique


class LocationDatabaseImporter:
    """Импортер локаций в базу данных"""

    # Namespace для генерации детерминированных UUID
    YANDEX_NAMESPACE = uuid.UUID("a1b2c3d4-e5f6-7890-abcd-ef1234567890")

    @staticmethod
    def map_transport_type(station_type: str, transport_type: str) -> LocationType:
        """Маппинг типа локации из Яндекс.Расписаний в LocationType."""
        if transport_type == "train" or transport_type == "suburban":
            return LocationType.railway_station
        elif transport_type == "plane":
            return LocationType.airport
        elif transport_type == "bus":
            return LocationType.bus_station
        elif transport_type == "water":
            pass
        elif transport_type == "helicopter":
            return LocationType.airport
        elif transport_type == "city":
            return LocationType.city

        # fallback на основе station_type
        if station_type in ("train_station", "suburban_station"):
            return LocationType.railway_station
        elif station_type == "airport":
            return LocationType.airport
        elif station_type in ("bus_station", "bus_stop"):
            return LocationType.bus_station
        elif station_type in ("river_port", "marine_station"):
            pass
        elif station_type == "city":
            return LocationType.city
        return LocationType.railway_station

    @staticmethod
    def get_city_name(location: dict[str, Any]) -> str | None:
        """Извлечение названия города."""
        city = location.get("city", "").strip()
        return city if city else None

    @staticmethod
    def get_timezone(location: dict[str, Any]) -> str:
        """Определение часового пояса (упрощенно, по стране)."""
        country = location.get("country", "")
        # Для РФ пока московское время
        if country == "Россия":
            return "Europe/Moscow"
        return "UTC"

    async def import_to_database(
        self,
        session_factory: async_sessionmaker[AsyncSession],
        locations: list[dict[str, Any]],
    ) -> dict[str, int]:
        """
        Импорт локаций в базу данных.

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
            for loc in locations:
                try:
                    yandex_code = str(loc.get("yandex_code"))
                    name = str(loc.get("name"))

                    if not yandex_code or not name:
                        stats["skipped"] += 1
                        continue

                    # Проверяем существует ли локация по yandex_code
                    # result = await session.execute(
                    #     select(Location).where(Location.code == yandex_code)
                    # )
                    # existing = result.scalar_one_or_none()

                    # Генерируем детерминированный UUID
                    location_id = uuid.uuid5(self.YANDEX_NAMESPACE, yandex_code)

                    location = Location(
                        id=location_id,
                        code=yandex_code,
                        name=name,
                        city_name=self.get_city_name(loc),
                        country_code="RU" if loc.get("country") == "Россия" else None,
                        location_type=self.map_transport_type(
                            loc.get("station_type", ""),
                            loc.get("transport_type", ""),
                        ),
                        lat=loc.get("lat"),
                        lon=loc.get("lon"),
                        timezone=self.get_timezone(loc),
                        is_hub=False,
                    )
                    session.add(location)
                    stats["added"] += 1

                except Exception as e:
                    logger.error(
                        f"Error importing location {loc.get('name')} "
                        f"({loc.get('yandex_code')}): {e}"
                    )
                    stats["errors"] += 1

            await session.commit()

        return stats


def save_to_json(locations: list[dict[str, Any]], filename: str | None = None) -> str:
    """Сохранение локаций в JSON файл."""
    if filename is None:
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        filename = f"yandex_rasp_locations_{timestamp}.json"

    with open(filename, "w", encoding="utf-8") as f:
        json.dump(locations, f, ensure_ascii=False, indent=2)

    logger.info(f"Saved {len(locations)} locations to {filename}")
    return filename


def load_from_json(filename: str) -> list[dict[str, Any]]:
    """Загрузка локаций из JSON файла."""
    with open(filename, encoding="utf-8") as f:
        locations = cast(list[dict[str, Any]], json.load(f))
    logger.info(f"Loaded {len(locations)} locations from {filename}")
    return locations


def main() -> None:
    asyncio.run(_main_async())


async def _main_async() -> None:
    args = _parse_args()

    logging.basicConfig(
        level=logging.INFO if not args.verbose else logging.DEBUG,
        format="%(asctime)s - %(levelname)s - %(message)s",
        handlers=[logging.StreamHandler(sys.stdout)],
    )

    settings = get_settings()
    engine = build_engine(settings)
    session_factory = build_session_factory(engine)

    try:
        # Определяем источник данных
        if args.input_file:
            # Режим: импорт из существующего JSON файла
            logger.info(f"Loading locations from file: {args.input_file}")
            locations = load_from_json(args.input_file)
        else:
            # Режим: загрузка из API
            api_key = settings.yandex_rasp_api_key
            if not api_key:
                logger.error(
                    "PDAXENIX_YANDEX_RASP_API_KEY not set in .env file.\n"
                    "Get your key at: https://yandex.ru/dev/rasp/"
                )
                sys.exit(1)

            fetcher = YandexRaspLocationFetcher(api_key)
            parser = ResponseParser()

            logger.info(
                "Fetching stations from Yandex Rasp API (this takes a while)..."
            )
            raw_data = await fetcher.fetch_all_stations()

            locations = parser.extract_locations(raw_data)
            logger.info(f"Extracted {len(locations)} locations")

            # Сохраняем в JSON если нужно
            if args.save_json or not args.import_db:
                logger.info(
                    "Enriching city records with full addresses from Yandex suggests..."
                )
                enricher = YandexRaspSuggestEnricher()
                enrichment_stats = await enricher.enrich_city_addresses(locations)
                logger.info(
                    "City enrichment completed: "
                    "cities=%s requests=%s enriched=%s missing=%s errors=%s",
                    enrichment_stats["city_locations"],
                    enrichment_stats["requests"],
                    enrichment_stats["enriched"],
                    enrichment_stats["missing"],
                    enrichment_stats["errors"],
                )

                filtered_locations = []

                # пока что удаляем города без full_address для простоты
                for loc in locations:
                    if (
                        loc.get("station_type") == "city"
                        and loc.get("full_address") is None
                    ):
                        continue
                    else:
                        filtered_locations.append(loc)

                filename = args.output_file if args.output_file else None
                saved_file = save_to_json(filtered_locations, filename)
                logger.info(f"Locations saved to: {saved_file}")

        # Импорт в БД если нужно
        if args.import_db:
            logger.info("Starting database import...")
            importer = LocationDatabaseImporter()
            stats = await importer.import_to_database(session_factory, locations)

            print("\n=== Yandex Rasp Locations Import Results ===")
            print(f"Total locations found:   {stats['total']}")
            print(f"New locations added:     {stats['added']}")
            print(f"Already existing:        {stats['skipped']}")
            print(f"Errors:                  {stats['errors']}")

    finally:
        await dispose_engine(engine)


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Import locations from Yandex Rasp API into JSON/DB.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  # Fetch from API and save to JSON only (default)
  python app/scripts/import_yandex_locations.py

  # Fetch from API and import to database
  python app/scripts/import_yandex_locations.py --import-db

  # Fetch from API, save to JSON and import to database
  python app/scripts/import_yandex_locations.py --save-json --import-db

  # Import from existing JSON file to database
  python app/scripts/import_yandex_locations.py \
    --input-file yandex_rasp_locations_20260426.json --import-db
        """,
    )

    # Источник данных
    parser.add_argument(
        "--input-file",
        type=str,
        help="Input JSON file with locations (skip API fetch)",
    )

    # Действия
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

    # Дополнительно
    parser.add_argument(
        "--output-file",
        type=str,
        help="Output JSON file name",
    )
    parser.add_argument(
        "--verbose",
        action="store_true",
        help="Enable verbose logging",
    )

    return parser.parse_args()


if __name__ == "__main__":
    main()
