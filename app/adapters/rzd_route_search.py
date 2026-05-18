from __future__ import annotations

import asyncio
import logging
from collections.abc import Mapping, Sequence
from datetime import UTC, datetime
from decimal import Decimal
from typing import Any, cast

import httpx
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.adapters.provider_route_support import (
    build_provider_segment_id,
    has_transfer_marker,
    resolve_provider_location,
    resolve_provider_route_duration_minutes,
    resolve_provider_route_total_price,
)
from app.clients.rzd_client_factory import RzdConfig, RzdHttpClientFactory
from app.models.enums import TransportType
from app.models.location import Location
from app.repositories.location_repository import LocationRepository
from app.services.application.ports import RouteSearchPort
from app.services.search.contracts import (
    ProviderRouteSegment,
    RouteCandidate,
    RouteSearchCriteria,
)
from app.utils.time_utils import timespan_to_minutes

logger = logging.getLogger(__name__)

RzdPayload = dict[str, Any]

_TRANSFER_LEG_KEYS = ("details", "legs", "segments", "path")
_TRANSFER_MARKER_KEYS = (
    "transfers",
    "transfer",
    "changes",
    "change",
    "hasTransfers",
    "has_transfer",
    "withTransfer",
)


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


class RzdRouteSearchAdapter(RouteSearchPort):
    """Адаптер для поиска маршрутов через API РЖД"""

    BASE_URL = "https://pass.rzd.ru/timetable/public"

    ROUTES_LAYER = 5827
    CARRIAGES_LAYER = 5764

    def __init__(
        self,
        http_client_factory: RzdHttpClientFactory,
        database_session_factory: async_sessionmaker[AsyncSession],
        config: RzdConfig | None = None,
    ):
        """
        Инициализация адаптера

        Args:
            http_client_factory: Фабрика для создания AsyncClient
            database_session_factory: Фабрика для создания БД-сессии
            config: Конфигурация API РЖД
        """
        self.config = config or RzdConfig()
        self._http_client_factory = http_client_factory
        self._database_session_factory = database_session_factory

    async def _get_session(self) -> httpx.AsyncClient:
        return await self._http_client_factory.get()

    async def _load_search_inputs(
        self,
        criteria: RouteSearchCriteria,
    ) -> tuple[Location | None, Location | None, str | None, str | None]:
        async with self._database_session_factory() as session:
            repository = LocationRepository(session)
            origin = await repository.get_by_id(criteria.origin_id)
            destination = await repository.get_by_id(criteria.destination_id)
            return (
                origin,
                destination,
                origin.rzd_code if origin is not None else None,
                destination.rzd_code if destination is not None else None,
            )

    async def _load_locations_by_codes(
        self,
        codes: tuple[str, ...],
    ) -> dict[str, Location]:
        async with self._database_session_factory() as session:
            repository = LocationRepository(session)
            return await repository.list_by_rzd_codes(codes)

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

        (
            requested_origin,
            requested_destination,
            origin_code,
            destination_code,
        ) = await self._load_search_inputs(criteria)

        if not origin_code or not destination_code:
            logger.debug(
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
        }

        response_data = await self._fetch_route_payloads(
            request_params=params,
            include_transfers=(criteria.preferences.max_transfers or 0) > 0,
        )
        if not response_data:
            logger.warning("No routes found or API error")
            return []

        locations_by_code = await self._load_locations_by_codes(
            _collect_rzd_codes_from_payloads(response_data)
        )
        routes = self._collect_candidates_from_payloads(
            payloads=response_data,
            requested_origin=requested_origin,
            requested_destination=requested_destination,
            requested_origin_code=origin_code,
            requested_destination_code=destination_code,
            locations_by_code=locations_by_code,
        )

        logger.debug(
            "RZD API route search completed candidate_count=%s",
            len(routes),
        )

        return routes

    async def _fetch_route_payloads(
        self,
        *,
        request_params: dict[str, Any],
        include_transfers: bool,
    ) -> list[RzdPayload]:
        if not include_transfers:
            return [await self._fetch_routes({**request_params, "md": 0})]

        # `md=1` returns transfer routes, but does not reliably include direct ones,
        # so transfer mode explicitly fetches both result sets.
        request_variants = (
            {**request_params, "md": 0},
            {**request_params, "md": 1},
        )
        results = await asyncio.gather(
            *(self._fetch_routes(params) for params in request_variants),
            return_exceptions=True,
        )

        successful_payloads: list[RzdPayload] = []
        errors: list[Exception] = []
        for params, result in zip(request_variants, results, strict=True):
            if isinstance(result, Exception):
                logger.warning(
                    "RZD API request failed md=%s error=%s",
                    params["md"],
                    result,
                )
                errors.append(result)
                continue
            successful_payloads.append(cast(RzdPayload, result))

        if successful_payloads:
            return successful_payloads
        if errors:
            raise errors[0]
        return []

    def _collect_candidates_from_payloads(
        self,
        *,
        payloads: Sequence[RzdPayload],
        requested_origin: Location | None,
        requested_destination: Location | None,
        requested_origin_code: str,
        requested_destination_code: str,
        locations_by_code: Mapping[str, Location],
    ) -> list[RouteCandidate]:
        routes: list[RouteCandidate] = []
        for payload in payloads:
            routes.extend(
                self._parse_routes_response(
                    payload,
                    requested_origin=requested_origin,
                    requested_destination=requested_destination,
                    requested_origin_code=requested_origin_code,
                    requested_destination_code=requested_destination_code,
                    locations_by_code=locations_by_code,
                )
            )
        return _dedupe_candidates_by_segment_ids(routes)

    def _parse_routes_response(
        self,
        response_data: dict[str, Any],
        *,
        requested_origin: Location | None,
        requested_destination: Location | None,
        requested_origin_code: str,
        requested_destination_code: str,
        locations_by_code: Mapping[str, Location],
    ) -> list[RouteCandidate]:
        """
        Парсинг ответа API и преобразование в RouteCandidate

        Args:
            response_data: Ответ от API РЖД

        Returns:
            Список маршрутов
        """
        tp_data = response_data.get("tp", [])
        if not tp_data:
            logger.warning("No tp data in response")
            return []

        routes: list[RouteCandidate] = []
        for route_data in _iter_rzd_routes(response_data):
            try:
                candidate = _build_rzd_candidate(
                    route_data=route_data,
                    requested_origin=requested_origin,
                    requested_destination=requested_destination,
                    requested_origin_code=requested_origin_code,
                    requested_destination_code=requested_destination_code,
                    locations_by_code=locations_by_code,
                )
                if candidate is not None:
                    routes.append(candidate)
            except Exception as e:
                logger.error("Error parsing route data: %s, data: %s", e, route_data)
                continue

        return routes


def _build_rzd_candidate(
    *,
    route_data: Mapping[str, Any],
    requested_origin: Location | None,
    requested_destination: Location | None,
    requested_origin_code: str,
    requested_destination_code: str,
    locations_by_code: Mapping[str, Location],
) -> RouteCandidate | None:
    raw_legs = _resolve_rzd_raw_legs(route_data)
    if raw_legs is None:
        return None

    provider_segments = _build_rzd_provider_segments(
        raw_legs=raw_legs,
        requested_origin=requested_origin,
        requested_destination=requested_destination,
        requested_origin_code=requested_origin_code,
        requested_destination_code=requested_destination_code,
        locations_by_code=locations_by_code,
    )
    if provider_segments is None:
        return None

    route_total_price = _resolve_rzd_route_total_price(
        route_data,
        provider_segments,
    )
    return RouteCandidate(
        source="rzd_api",
        segment_ids=tuple(segment.segment_id for segment in provider_segments),
        total_price=route_total_price,
        total_duration_minutes=_resolve_rzd_route_duration_minutes(
            route_data,
            provider_segments,
        ),
        transfers=len(provider_segments) - 1,
        resolved_segments=provider_segments,
    )


def _resolve_rzd_raw_legs(
    route_data: Mapping[str, Any],
) -> tuple[dict[str, Any], ...] | None:
    raw_legs = _extract_rzd_raw_legs(route_data)
    if raw_legs is not None:
        return raw_legs
    if has_transfer_marker(route_data, _TRANSFER_MARKER_KEYS):
        logger.debug("Skipping RZD transfer route without explicit legs")
        return None
    return (dict(route_data),)


def _build_rzd_provider_segments(
    *,
    raw_legs: Sequence[Mapping[str, Any]],
    requested_origin: Location | None,
    requested_destination: Location | None,
    requested_origin_code: str,
    requested_destination_code: str,
    locations_by_code: Mapping[str, Location],
) -> tuple[ProviderRouteSegment, ...] | None:
    provider_segments: list[ProviderRouteSegment] = []
    for index, raw_leg in enumerate(raw_legs):
        provider_segment = _build_rzd_provider_segment(
            raw_leg,
            requested_origin=requested_origin,
            requested_destination=requested_destination,
            requested_origin_code=requested_origin_code,
            requested_destination_code=requested_destination_code,
            locations_by_code=locations_by_code,
            is_first=index == 0,
            is_last=index == len(raw_legs) - 1,
        )
        if provider_segment is None:
            return None
        provider_segments.append(provider_segment)
    return tuple(provider_segments)


def _collect_rzd_codes(payload: RzdPayload) -> tuple[str, ...]:
    codes: set[str] = set()
    for route_data in _iter_rzd_routes(payload):
        raw_legs = _extract_rzd_raw_legs(route_data) or (route_data,)
        for raw_leg in raw_legs:
            origin_code = _extract_rzd_location_code(raw_leg, side=0)
            destination_code = _extract_rzd_location_code(raw_leg, side=1)
            if origin_code:
                codes.add(origin_code)
            if destination_code:
                codes.add(destination_code)
    return tuple(sorted(codes))


def _collect_rzd_codes_from_payloads(payloads: Sequence[RzdPayload]) -> tuple[str, ...]:
    codes: set[str] = set()
    for payload in payloads:
        codes.update(_collect_rzd_codes(payload))
    return tuple(sorted(codes))


def _dedupe_candidates_by_segment_ids(
    candidates: Sequence[RouteCandidate],
) -> list[RouteCandidate]:
    deduped: list[RouteCandidate] = []
    seen_segment_ids: set[tuple[object, ...]] = set()
    for candidate in candidates:
        dedupe_key = tuple(candidate.segment_ids)
        if dedupe_key in seen_segment_ids:
            continue
        seen_segment_ids.add(dedupe_key)
        deduped.append(candidate)
    return deduped


def _iter_rzd_routes(payload: RzdPayload) -> list[Mapping[str, Any]]:
    tp_data = payload.get("tp", [])
    if not tp_data:
        return []
    first_tp_item = tp_data[0]
    if not isinstance(first_tp_item, dict):
        return []
    raw_routes = first_tp_item.get("list", [])
    return [route for route in raw_routes if isinstance(route, dict)]


def _extract_rzd_raw_legs(
    route_data: Mapping[str, Any],
) -> tuple[dict[str, Any], ...] | None:
    for key in _TRANSFER_LEG_KEYS:
        raw_value = route_data.get(key)
        if not isinstance(raw_value, list):
            continue
        raw_legs = tuple(item for item in raw_value if isinstance(item, dict))
        if raw_legs:
            return raw_legs
    return None


def _build_rzd_provider_segment(
    raw_leg: Mapping[str, Any],
    *,
    requested_origin: Location | None,
    requested_destination: Location | None,
    requested_origin_code: str,
    requested_destination_code: str,
    locations_by_code: Mapping[str, Location],
    is_first: bool,
    is_last: bool,
) -> ProviderRouteSegment | None:
    departure_at = _parse_rzd_datetime(
        date_value=raw_leg.get("date0"),
        time_value=raw_leg.get("time0"),
    )
    arrival_at = _parse_rzd_datetime(
        date_value=raw_leg.get("date1"),
        time_value=raw_leg.get("time1"),
    )
    if departure_at is None or arrival_at is None:
        logger.debug("Skipping RZD leg because departure or arrival is missing")
        return None

    origin_code = _extract_rzd_location_code(raw_leg, side=0)
    destination_code = _extract_rzd_location_code(raw_leg, side=1)
    origin_location = resolve_provider_location(
        code=origin_code,
        requested_code=requested_origin_code if is_first else None,
        requested_location=requested_origin if is_first else None,
        locations_by_code=locations_by_code,
    )
    destination_location = resolve_provider_location(
        code=destination_code,
        requested_code=requested_destination_code if is_last else None,
        requested_location=requested_destination if is_last else None,
        locations_by_code=locations_by_code,
    )
    if origin_location is None or destination_location is None:
        logger.debug(
            "Skipping RZD leg because canonical locations are unresolved "
            "origin_code=%s destination_code=%s",
            origin_code,
            destination_code,
        )
        return None

    segment_code = raw_leg.get("number")
    source_record_id = raw_leg.get("trainId") or raw_leg.get("number")
    tariff = _extract_rzd_tariff(raw_leg)
    currency_code = str(raw_leg.get("currency") or "RUB")
    return ProviderRouteSegment(
        segment_id=build_provider_segment_id(
            source="rzd_api",
            origin_code=origin_location.code,
            destination_code=destination_location.code,
            departure_at=departure_at,
            arrival_at=arrival_at,
            segment_code=segment_code,
        ),
        transport_type=TransportType.train,
        carrier_name=str(raw_leg.get("carrier") or "RZD"),
        carrier_code=None,
        segment_code=segment_code,
        origin_location=origin_location,
        destination_location=destination_location,
        departure_at=departure_at,
        arrival_at=arrival_at,
        duration_minutes=_extract_rzd_duration_minutes(
            raw_leg,
            departure_at=departure_at,
            arrival_at=arrival_at,
        ),
        price_amount=tariff,
        currency_code=currency_code,
        available_seats=_extract_rzd_available_seats(raw_leg),
        source_system="rzd_api",
        source_record_id=str(source_record_id)
        if source_record_id is not None
        else None,
        valid_from=datetime.now(UTC),
        valid_to=None,
    )


def _parse_rzd_datetime(
    *,
    date_value: Any,
    time_value: Any,
) -> datetime | None:
    if not date_value or not time_value:
        return None
    try:
        return datetime.strptime(f"{date_value} {time_value}", "%d.%m.%Y %H:%M")
    except ValueError:
        return None


def _extract_rzd_duration_minutes(
    raw_leg: Mapping[str, Any],
    *,
    departure_at: datetime,
    arrival_at: datetime,
) -> int:
    time_in_way = raw_leg.get("timeInWay")
    if time_in_way:
        return timespan_to_minutes(str(time_in_way))
    return int((arrival_at - departure_at).total_seconds() // 60)


def _extract_rzd_tariff(raw_leg: Mapping[str, Any]) -> Decimal | None:
    cars = raw_leg.get("cars")
    # здесь может быть пусто в случае электричек
    if isinstance(cars, list) and cars:
        tariff = cars[0].get("tariff")
        if tariff is not None:
            return Decimal(str(tariff))
    tariff = raw_leg.get("tariff")
    if tariff is None:
        return None
    return Decimal(str(tariff))


def _extract_rzd_available_seats(raw_leg: Mapping[str, Any]) -> int | None:
    cars = raw_leg.get("cars")
    if isinstance(cars, list) and cars:
        free_seats = cars[0].get("freeSeats")
        return int(free_seats) if free_seats is not None else None
    free_seats = raw_leg.get("freeSeats")
    return int(free_seats) if free_seats is not None else None


def _extract_rzd_location_code(raw_leg: Mapping[str, Any], *, side: int) -> str | None:
    candidate_keys = (
        f"code{side}",
        "fromCode" if side == 0 else "whereCode",
        "code0" if side == 0 else "code1",
    )
    for key in candidate_keys:
        value = raw_leg.get(key)
        if value:
            return str(value)
    return None


def _resolve_rzd_route_total_price(
    route_data: Mapping[str, Any],
    provider_segments: Sequence[ProviderRouteSegment],
) -> Decimal | None:
    return resolve_provider_route_total_price(
        route_total_price=_extract_rzd_tariff(route_data),
        provider_segments=provider_segments,
    )


def _resolve_rzd_route_duration_minutes(
    route_data: Mapping[str, Any],
    provider_segments: Sequence[ProviderRouteSegment],
) -> int:
    explicit_duration_minutes = None
    if route_data.get("timeInWay"):
        explicit_duration_minutes = timespan_to_minutes(str(route_data["timeInWay"]))
    return resolve_provider_route_duration_minutes(
        explicit_duration_minutes=explicit_duration_minutes,
        provider_segments=provider_segments,
    )
