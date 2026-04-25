import asyncio
import logging
import uuid
from datetime import datetime
from decimal import Decimal
from typing import Any

import httpx

from app.clients.rzd_client_factory import RzdConfig, RzdHttpClientFactory
from app.models.carrier import Carrier
from app.models.enums import TransportType
from app.models.location import Location
from app.models.route_segment import RouteSegment
from app.services.models import RouteCandidate, RouteSearchCriteria
from app.utils.time_utils import timespan_to_minutes

logger = logging.getLogger(__name__)


class RZDApiError(Exception):
    """Базовое исключение для ошибок API РЖД"""

    pass


class RZDRequestError(RZDApiError):
    """Ошибка при выполнении запроса"""

    pass


class RZDResponseError(RZDApiError):
    """Ошибка в ответе API"""

    pass


class RZDTimeoutError(RZDApiError):
    """Тайм-аут при ожидании данных"""

    pass


class RzdRouteSearchAdapter:
    """Адаптер для поиска маршрутов через API РЖД"""

    BASE_URL = "https://pass.rzd.ru/timetable/public"
    SUGGESTION_URL = "https://pass.rzd.ru/suggester"
    STATION_LIST_URL = "https://pass.rzd.ru/ticket/services/route/basicRoute"

    ROUTES_LAYER = 5827
    CARRIAGES_LAYER = 5764
    STATIONS_STRUCTURE_ID = 704

    def __init__(
        self,
        station_code_mapping: dict[uuid.UUID, str],
        http_client_factory: RzdHttpClientFactory,
        config: RzdConfig | None = None,
    ):
        """
        Инициализация адаптера

        Args:
            config: Конфигурация API РЖД
            station_code_mapping: Словарь для маппинга station_id -> код станции РЖД
            http_client_factory: Фабрика для создания AsyncClient
        """
        self.config = config or RzdConfig()
        self.station_code_mapping = station_code_mapping
        self._http_client_factory = http_client_factory

    async def _get_session(self) -> httpx.AsyncClient:
        return await self._http_client_factory.get()

    async def _get_station_code(self, station_id: uuid.UUID) -> str | None:
        """
        Получение кода станции РЖД по ID из БД

        Args:
            station_id: ID станции

        Returns:
            Код станции для API РЖД или None
        """
        return self.station_code_mapping.get(station_id)

    async def _fetch_routes(self, params: dict[str, Any]) -> Any:
        """
        Выполнение запроса к API РЖД для получения маршрутов

        Args:
            params: Параметры запроса

        Returns:
            Ответ API в виде словаря

        Raises:
            RZDRequestError: При ошибках HTTP запроса
            RZDResponseError: При ошибках в ответе API
            RZDTimeoutError: При превышении попыток получения данных по RID
        """
        session = await self._get_session()
        url = f"{self.BASE_URL}/{self.config.language}"

        params["layer_id"] = str(self.ROUTES_LAYER)

        try:
            logger.debug(f"Requesting RZD API: {url} with params: {params}")

            response: httpx.Response = await session.post(url, data=params)
            response.raise_for_status()

            data = response.json()

            result = data.get("result", "OK")
            # экспериментально подтверждено, что ответ обычно доходит дольше секунды
            delay = 1.0
            data_request_attempts = 0
            last_attempt = False

            while result in ["RID", "REQUEST_ID"] and not last_attempt:
                if delay == self.config.timeout:
                    last_attempt = True

                rid = data.get("rid") or data.get("RID")
                if not rid:
                    raise RZDResponseError("RID not found in response")

                logger.debug(f"Got RID: {rid}, waiting for data...")
                params = {"rid": rid, "layer_id": str(self.ROUTES_LAYER)}

                await asyncio.sleep(delay)
                delay = min(delay * 1.5, self.config.timeout)

                response = await session.post(url, data=params)
                response.raise_for_status()
                data = response.json()
                result = data.get("result", "OK")
                data_request_attempts += 1

            if result in ["RID", "REQUEST_ID"]:
                raise RZDTimeoutError(
                    f"Failed to get data after {data_request_attempts} attempts"
                )

            if result != "OK":
                error_msg = (
                    data.get("tp", [{}])[0]
                    .get("msgList", [{}])[0]
                    .get("message", "Failed to get request data")
                )
                raise RZDResponseError(f"RZD API error: {error_msg}")

            logger.debug(
                "RZD API client used %s attempts to get data",
                data_request_attempts,
            )

            return data

        except httpx.HTTPStatusError as e:
            logger.error(
                f"HTTP status error: {e.response.status_code} - {e.response.text}"
            )
            raise RZDRequestError(
                f"HTTP {e.response.status_code}: Failed to fetch routes"
            ) from e
        except httpx.HTTPError as e:
            logger.error(f"HTTP error while fetching routes: {e}")
            raise RZDRequestError("Network error while fetching routes") from e
        except RZDApiError:
            raise
        except Exception as e:
            logger.error(f"Unexpected error: {e}")
            raise RZDApiError(f"Unexpected error: {e}") from e

    async def search(self, criteria: RouteSearchCriteria) -> list[RouteCandidate]:
        """
        Поиск маршрутов через API РЖД

        Args:
            criteria: Критерии поиска маршрута

        Returns:
            Список найденных маршрутов
        """
        logger.debug(
            """
                    RZD API route search started 
                    origin_id=%s 
                    destination_id=%s 
                    travel_date=%s
                 """,
            criteria.origin_id,
            criteria.destination_id,
            criteria.travel_date,
        )

        # Получаем коды станций для API РЖД
        origin_code = await self._get_station_code(criteria.origin_id)
        destination_code = await self._get_station_code(criteria.destination_id)

        if not origin_code or not destination_code:
            logger.error(
                "Station codes not found: origin=%s (%s), destination=%s (%s)",
                criteria.origin_id,
                origin_code,
                criteria.destination_id,
                destination_code,
            )
            return []

        params = {
            "dir": 0,  # 0 - только в один конец
            "tfl": 3,  # 3 - поезда и электрички
            "checkSeats": 1,  # 1 - только с билетами
            "code0": origin_code,
            "code1": destination_code,
            "dt0": criteria.travel_date.strftime("%d.%m.%Y"),
            "md": 0,  # 0 - без пересадок
        }

        response_data = await self._fetch_routes(params)

        if not response_data:
            logger.warning("No routes found or API error")
            return []

        routes = await self._parse_routes_response(response_data)

        logger.debug(
            "RZD API route search completed candidate_count=%s",
            len(routes),
        )

        return routes

    async def _parse_routes_response(
        self,
        response_data: dict[str, Any],
    ) -> list[RouteCandidate]:
        """
        Парсинг ответа API и преобразование в RouteCandidate

        Args:
            response_data: Ответ от API РЖД

        Returns:
            Список маршрутов
        """
        routes = []

        # В ответе данные находятся в tp[0].list
        tp_data = response_data.get("tp", [])
        if not tp_data:
            logger.warning("No tp data in response")
            return []

        routes_list = tp_data[0].get("list", [])

        for route_data in routes_list:
            segment = RouteSegment(
                id=uuid.uuid4(),
                transport_type=TransportType.train,
                carrier=Carrier(name=route_data.get("carrier"), code=None),
                segment_code=None,
                origin_location=Location(
                    id=uuid.uuid4(), name=route_data.get("station0"), code=None
                ),
                destination_location=Location(
                    id=uuid.uuid4(), name=route_data.get("station1"), code=None
                ),
                departure_at=datetime.strptime(
                    f"{route_data.get('date0')} {route_data.get('time0')}",
                    "%d.%m.%Y %H:%M",
                ),
                arrival_at=datetime.strptime(
                    f"{route_data.get('date1')} {route_data.get('time1')}",
                    "%d.%m.%Y %H:%M",
                ),
                duration_minutes=timespan_to_minutes(route_data.get("timeInWay")),
                price_amount=Decimal(route_data.get("cars", [{}])[0].get("tariff")),
                currency_code="RUB",
                available_seats=route_data.get("cars", [{}])[0].get("freeSeats"),
                source_system="rzd_api",
                source_record_id=None,
                valid_from=datetime.now(),
                valid_to=None,
            )

            try:
                route_candidate = RouteCandidate(
                    source="rzd_api",
                    segment_ids=(segment.id,),
                    total_price=route_data.get("cars", [{}])[0].get("tariff"),
                    total_duration_minutes=timespan_to_minutes(
                        route_data.get("timeInWay")
                    ),
                    transfers=0,
                    resolved_segments=(segment,),
                )

                routes.append(route_candidate)

            except Exception as e:
                logger.error(f"Error parsing route data: {e}, data: {route_data}")
                continue

        return routes
