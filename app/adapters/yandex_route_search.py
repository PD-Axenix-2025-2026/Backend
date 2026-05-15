from __future__ import annotations

import logging
from collections.abc import Mapping, Sequence
from datetime import UTC, datetime
from decimal import Decimal
from typing import Any

import httpx
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.adapters.provider_route_support import (
    build_provider_segment_id,
    has_transfer_marker,
    resolve_provider_location,
    resolve_provider_route_duration_minutes,
    resolve_provider_route_total_price,
)
from app.models.enums import TransportType
from app.models.location import Location
from app.repositories.location_repository import LocationRepository
from app.services.application.ports import RouteSearchPort
from app.services.search.contracts import (
    ProviderRouteSegment,
    RouteCandidate,
    RouteSearchCriteria,
)

logger = logging.getLogger(__name__)

_TRANSFER_LEG_KEYS = ("details", "legs", "segments")
_TRANSFER_MARKER_KEYS = (
    "has_transfers",
    "transfers",
    "hasTransfers",
    "transfer",
    "with_transfer",
    "withTransfer",
)


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
                origin.yandex_code if origin is not None else None,
                destination.yandex_code if destination is not None else None,
            )

    async def _load_locations_by_codes(
        self,
        codes: tuple[str, ...],
    ) -> dict[str, Location]:
        async with self._database_session_factory() as session:
            repository = LocationRepository(session)
            return await repository.list_by_yandex_codes(codes)

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

        (
            requested_origin,
            requested_destination,
            from_code,
            to_code,
        ) = await self._load_search_inputs(criteria)

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
            "transfers": (criteria.preferences.max_transfers or 0) > 0,
        }

        response_data = await self._fetch_routes(params)

        if not response_data:
            logger.warning("No routes found")
            return []

        locations_by_code = await self._load_locations_by_codes(
            _collect_yandex_codes(response_data)
        )
        routes = self._parse_response(
            response_data,
            requested_origin=requested_origin,
            requested_destination=requested_destination,
            requested_origin_code=from_code,
            requested_destination_code=to_code,
            locations_by_code=locations_by_code,
        )
        logger.debug("Yandex Rasp search completed candidate_count=%s", len(routes))
        return routes

    def _parse_response(
        self,
        data: dict[str, Any],
        *,
        requested_origin: Location | None,
        requested_destination: Location | None,
        requested_origin_code: str,
        requested_destination_code: str,
        locations_by_code: Mapping[str, Location],
    ) -> list[RouteCandidate]:
        routes: list[RouteCandidate] = []
        for route_data in data.get("segments", []):
            try:
                candidate = _build_yandex_candidate(
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
                logger.error("Error parsing segment: %s, data: %s", e, route_data)
                continue

        return routes


def _build_yandex_candidate(
    *,
    route_data: Mapping[str, Any],
    requested_origin: Location | None,
    requested_destination: Location | None,
    requested_origin_code: str,
    requested_destination_code: str,
    locations_by_code: Mapping[str, Location],
) -> RouteCandidate | None:
    raw_legs = _resolve_yandex_raw_legs(route_data)
    if raw_legs is None:
        return None

    provider_segments = _build_yandex_provider_segments(
        raw_legs=raw_legs,
        requested_origin=requested_origin,
        requested_destination=requested_destination,
        requested_origin_code=requested_origin_code,
        requested_destination_code=requested_destination_code,
        locations_by_code=locations_by_code,
    )
    if provider_segments is None:
        return None

    route_total_price = _resolve_route_total_price(
        route_data,
        provider_segments,
    )
    return RouteCandidate(
        source="yandex_rasp_api",
        segment_ids=tuple(segment.segment_id for segment in provider_segments),
        total_price=route_total_price,
        total_duration_minutes=_resolve_route_duration_minutes(
            route_data,
            provider_segments,
        ),
        transfers=len(provider_segments) - 1,
        resolved_segments=provider_segments,
    )


def _resolve_yandex_raw_legs(
    route_data: Mapping[str, Any],
) -> tuple[dict[str, Any], ...] | None:
    raw_legs = _extract_yandex_raw_legs(route_data)
    if raw_legs is not None:
        return raw_legs
    if has_transfer_marker(route_data, _TRANSFER_MARKER_KEYS):
        logger.debug("Skipping Yandex transfer route without explicit legs")
        return None
    return (dict(route_data),)


def _build_yandex_provider_segments(
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
        provider_segment = _build_yandex_provider_segment(
            raw_leg,
            requested_origin=requested_origin,
            requested_destination=requested_destination,
            requested_origin_code=requested_origin_code,
            requested_destination_code=requested_destination_code,
            locations_by_code=locations_by_code,
            source="yandex_rasp_api",
            is_first=index == 0,
            is_last=index == len(raw_legs) - 1,
        )
        if provider_segment is None:
            return None
        provider_segments.append(provider_segment)
    return tuple(provider_segments)


def _collect_yandex_codes(data: dict[str, Any]) -> tuple[str, ...]:
    codes: set[str] = set()
    for route_data in data.get("segments", []):
        raw_legs = _extract_yandex_raw_legs(route_data) or (route_data,)
        for raw_leg in raw_legs:
            from_code = raw_leg.get("from", {}).get("code")
            to_code = raw_leg.get("to", {}).get("code")
            if from_code:
                codes.add(str(from_code))
            if to_code:
                codes.add(str(to_code))
    return tuple(sorted(codes))


def _extract_yandex_raw_legs(
    route_data: Mapping[str, Any],
) -> tuple[dict[str, Any], ...] | None:
    for key in _TRANSFER_LEG_KEYS:
        raw_value = route_data.get(key)
        if not isinstance(raw_value, list):
            continue
        raw_legs = tuple(
            item
            for item in raw_value
            if isinstance(item, dict) and _is_yandex_travel_leg(item)
        )
        if raw_legs:
            return raw_legs
    return None


def _is_yandex_travel_leg(raw_leg: Mapping[str, Any]) -> bool:
    if raw_leg.get("is_transfer") is True:
        return False
    return "departure" in raw_leg and "arrival" in raw_leg


def _build_yandex_provider_segment(
    raw_leg: Mapping[str, Any],
    *,
    requested_origin: Location | None,
    requested_destination: Location | None,
    requested_origin_code: str,
    requested_destination_code: str,
    locations_by_code: Mapping[str, Location],
    source: str,
    is_first: bool,
    is_last: bool,
) -> ProviderRouteSegment | None:
    departure_dt = datetime.fromisoformat(raw_leg["departure"])
    arrival_dt = datetime.fromisoformat(raw_leg["arrival"])
    duration_seconds = raw_leg.get("duration", 0)
    duration_minutes = max(
        int(duration_seconds // 60),
        int((arrival_dt - departure_dt).total_seconds() // 60),
    )

    thread = raw_leg.get("thread", {})
    carrier_data = thread.get("carrier", {})
    tickets_info = raw_leg.get("tickets_info")
    currency_code = _extract_tickets_currency(tickets_info) or "RUB"

    origin_payload = raw_leg.get("from", {})
    destination_payload = raw_leg.get("to", {})
    origin_location = resolve_provider_location(
        code=_extract_location_code(origin_payload),
        requested_location=requested_origin if is_first else None,
        requested_code=requested_origin_code if is_first else None,
        locations_by_code=locations_by_code,
    )
    destination_location = resolve_provider_location(
        code=_extract_location_code(destination_payload),
        requested_location=requested_destination if is_last else None,
        requested_code=requested_destination_code if is_last else None,
        locations_by_code=locations_by_code,
    )
    if origin_location is None or destination_location is None:
        logger.debug(
            "Skipping Yandex leg because canonical locations are unresolved "
            "origin_code=%s destination_code=%s",
            origin_payload.get("code"),
            destination_payload.get("code"),
        )
        return None

    source_record_id = thread.get("uid") or thread.get("number")
    segment_code = thread.get("number")
    return ProviderRouteSegment(
        segment_id=build_provider_segment_id(
            source=source,
            origin_code=origin_location.code,
            destination_code=destination_location.code,
            departure_at=departure_dt,
            arrival_at=arrival_dt,
            segment_code=segment_code,
        ),
        transport_type=_map_transport_type(
            thread.get("transport_type")
            or origin_payload.get("transport_type")
            or destination_payload.get("transport_type"),
        ),
        carrier_name=carrier_data.get("title") or thread.get("title") or "Unknown",
        carrier_code=(
            str(carrier_data.get("code")) if carrier_data.get("code") else None
        ),
        segment_code=segment_code,
        origin_location=origin_location,
        destination_location=destination_location,
        departure_at=departure_dt,
        arrival_at=arrival_dt,
        duration_minutes=duration_minutes,
        price_amount=_extract_tickets_price(tickets_info),
        currency_code=currency_code,
        available_seats=None,
        source_system=source,
        source_record_id=source_record_id,
        valid_from=datetime.now(UTC),
        valid_to=None,
    )


def _extract_location_code(payload: Mapping[str, Any]) -> str | None:
    raw_code = payload.get("code")
    return str(raw_code) if raw_code is not None else None


def _extract_tickets_price(tickets_info: Any) -> Decimal | None:
    if not isinstance(tickets_info, dict):
        return None
    places = tickets_info.get("places")
    if not isinstance(places, list) or not places:
        return None
    price_info = places[0].get("price", {})
    return _to_decimal_price(
        whole=price_info.get("whole"),
        cents=price_info.get("cents"),
    )


def _extract_tickets_currency(tickets_info: Any) -> str | None:
    if not isinstance(tickets_info, dict):
        return None
    places = tickets_info.get("places")
    if not isinstance(places, list) or not places:
        return None
    currency = places[0].get("currency")
    if currency is None:
        return None
    return str(currency)


def _resolve_route_total_price(
    route_data: Mapping[str, Any],
    provider_segments: Sequence[ProviderRouteSegment],
) -> Decimal | None:
    return resolve_provider_route_total_price(
        route_total_price=_extract_tickets_price(route_data.get("tickets_info")),
        provider_segments=provider_segments,
    )


def _resolve_route_duration_minutes(
    route_data: Mapping[str, Any],
    provider_segments: Sequence[ProviderRouteSegment],
) -> int:
    raw_duration_seconds = route_data.get("duration")
    explicit_duration_minutes = None
    if isinstance(raw_duration_seconds, int | float):
        explicit_duration_minutes = int(raw_duration_seconds // 60)
    return resolve_provider_route_duration_minutes(
        explicit_duration_minutes=explicit_duration_minutes,
        provider_segments=provider_segments,
    )


def _to_decimal_price(
    *,
    whole: int | float | str | Decimal | None,
    cents: int | float | str | Decimal | None = 0,
) -> Decimal | None:
    if whole is None:
        return None

    whole_decimal = Decimal(str(whole))
    cents_decimal = Decimal(str(cents or 0)) / Decimal("100")
    return whole_decimal + cents_decimal


def _map_transport_type(transport_type: str) -> TransportType:
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
