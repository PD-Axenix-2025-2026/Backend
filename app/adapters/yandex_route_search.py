import logging
import uuid
from datetime import datetime
from decimal import Decimal
from typing import Any

import httpx
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.models.carrier import Carrier
from app.models.enums import TransportType
from app.models.location import Location
from app.models.route_segment import RouteSegment
from app.repositories.location_repository import LocationRepository
from app.services.models import RouteCandidate, RouteSearchCriteria
from app.services.ports import RouteSearchPort

logger = logging.getLogger(__name__)


class YandexRaspApiError(Exception):
    """Базовое исключение для ошибок API Яндекс.Расписаний"""

    pass


class YandexRaspRequestError(YandexRaspApiError):
    """Ошибка при выполнении запроса"""

    pass


class YandexRaspResponseError(YandexRaspApiError):
    """Ошибка в ответе API"""

    pass


class YandexRaspRouteSearchAdapter(RouteSearchPort):
    """Адаптер для поиска маршрутов через API Яндекс.Расписаний"""

    BASE_URL = "https://api.rasp.yandex-net.ru/v3.0/search/"

    def __init__(
        self,
        api_key: str,
        database_session_factory: async_sessionmaker[AsyncSession],
    ):
        self.api_key = api_key
        self._database_session_factory = database_session_factory

    async def _get_station_code(self, location_id: uuid.UUID) -> str | None:
        """Получение кода Яндекс.Расписаний для локации по её ID."""
        async with self._database_session_factory() as session:
            repository = LocationRepository(session)
            loc = await repository.get_by_id(location_id)
            if loc:
                return loc.yandex_code
            return None

    async def _fetch_routes(self, params: dict[str, Any]) -> Any:
        """
        Выполнение запроса к API Яндекс.Расписаний.

        Args:
            params: Параметры запроса (from, to, date, transport_types и т.д.)

        Returns:
            Ответ API в виде словаря

        Raises:
            YandexRaspRequestError: При ошибках HTTP запроса
            YandexRaspResponseError: При ошибках в ответе API
        """
        async with httpx.AsyncClient(timeout=30.0) as client:
            try:
                response = await client.get(
                    self.BASE_URL,
                    params={
                        "apikey": self.api_key,
                        "format": "json",
                        "lang": "ru_RU",
                        **params,
                    },
                    headers={"Accept": "application/json"},
                )
                response.raise_for_status()
                return response.json()

            except httpx.HTTPStatusError as e:
                logger.error(
                    f"HTTP error: {e.response.status_code} - {e.response.text}"
                )
                raise YandexRaspRequestError(
                    f"HTTP {e.response.status_code}: Failed to fetch routes"
                ) from e
            except httpx.HTTPError as e:
                logger.error(f"Network error while fetching routes: {e}")
                raise YandexRaspRequestError(
                    "Network error while fetching routes"
                ) from e
            except Exception as e:
                logger.error(f"Unexpected error: {e}")
                raise YandexRaspApiError(f"Unexpected error: {e}") from e

    async def search(self, criteria: RouteSearchCriteria) -> list[RouteCandidate]:
        """
        Поиск маршрутов через API Яндекс.Расписаний.

        Args:
            criteria: Критерии поиска (origin_id, destination_id, дата).

        Returns:
            Список RouteCandidate.
        """
        logger.debug(
            "Yandex Rasp search started origin=%s destination=%s date=%s",
            criteria.origin_id,
            criteria.destination_id,
            criteria.travel_date,
        )

        from_code = await self._get_station_code(criteria.origin_id)
        to_code = await self._get_station_code(criteria.destination_id)

        if not from_code or not to_code:
            logger.debug(
                "Station codes not found: origin=%s (%s), destination=%s (%s)",
                criteria.origin_id,
                from_code,
                criteria.destination_id,
                to_code,
            )
            return []

        params = {
            "from": from_code,
            "to": to_code,
            "date": criteria.travel_date.strftime("%Y-%m-%d"),
            "transfers": False,
        }

        response_data = await self._fetch_routes(params)

        if not response_data:
            logger.warning("No routes found")
            return []

        routes = await self._parse_response(response_data)
        logger.debug("Yandex Rasp search completed candidate_count=%s", len(routes))
        return routes

    async def _parse_response(self, data: dict[str, Any]) -> list[RouteCandidate]:
        """
        Преобразование ответа API в список RouteCandidate.

        Структура ответа:
        {
            "search": { "from": {...}, "to": {...}, "date": "..." },
            "segments": [
                {
                    "from": {"title": "...", "code": "...", ...},
                    "to": {"title": "...", "code": "...", ...},
                    "departure": "2026-04-27T10:00:00+03:00",
                    "arrival": "2026-04-27T14:30:00+03:00",
                    "duration": 270,
                    "thread": {
                        "number": "123А",
                        "title": "Москва — Петербург",
                        "carrier": {"code": 1, "title": "РЖД"}
                    },
                    "tickets_info": {
                        "places": [{"price": {"whole": 2500, "cents": 0}}]
                    }
                },
                ...
            ]
        }
        """
        routes = []
        segments = data.get("segments", [])

        for segment_data in segments:
            try:
                departure_str = segment_data["departure"]
                arrival_str = segment_data["arrival"]
                departure_dt = datetime.fromisoformat(departure_str)
                arrival_dt = datetime.fromisoformat(arrival_str)

                duration_seconds = segment_data.get("duration", 0)
                duration_minutes = duration_seconds // 60

                thread = segment_data.get("thread", {})
                carrier_data = thread.get("carrier", {})

                price = Decimal("0")
                tickets_info = segment_data.get("tickets_info")
                if tickets_info and "places" in tickets_info and tickets_info["places"]:
                    first_place = tickets_info["places"][0]
                    price_info = first_place.get("price", {})
                    whole = price_info.get("whole", 0)
                    cents = price_info.get("cents", 0)
                    price = Decimal(f"{whole}.{cents:02d}")

                segment = RouteSegment(
                    id=uuid.uuid4(),
                    transport_type=self.map_transport_type(
                        segment_data["from"]["transport_type"]
                    ),
                    carrier=Carrier(
                        name=carrier_data.get("title"),
                        code=str(carrier_data.get("code"))
                        if carrier_data.get("code")
                        else None,
                    ),
                    segment_code=thread.get("number"),
                    origin_location=Location(
                        id=uuid.uuid4(),
                        name=segment_data["from"]["title"],
                        code=segment_data["from"].get("code"),
                    ),
                    destination_location=Location(
                        id=uuid.uuid4(),
                        name=segment_data["to"]["title"],
                        code=segment_data["to"].get("code"),
                    ),
                    departure_at=departure_dt,
                    arrival_at=arrival_dt,
                    duration_minutes=duration_minutes,
                    price_amount=price,
                    currency_code="RUB",
                    available_seats=None,  # API не дает информации про это
                    source_system="yandex_rasp_api",
                    source_record_id=None,
                    valid_from=datetime.now(),
                    valid_to=None,
                )

                route_candidate = RouteCandidate(
                    source="yandex_rasp_api",
                    segment_ids=(segment.id,),
                    total_price=segment.price_amount,
                    total_duration_minutes=segment.duration_minutes,
                    transfers=0,
                    resolved_segments=(segment,),
                )
                routes.append(route_candidate)

            except Exception as e:
                logger.error(f"Error parsing segment: {e}, data: {segment_data}")
                continue

        return routes

    def map_transport_type(self, transport_type: str) -> TransportType:
        """Маппинг типа транспорта из Яндекс.Расписаний в TransportType."""
        if transport_type == "train" or transport_type == "suburban":
            return TransportType.train
        elif transport_type == "plane":
            return TransportType.plane
        elif transport_type == "bus":
            return TransportType.bus
        elif transport_type == "water":
            pass
        elif transport_type == "helicopter":
            return TransportType.plane

        return TransportType.train
