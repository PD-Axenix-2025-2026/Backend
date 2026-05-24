from __future__ import annotations

import hashlib
import json
import logging
from datetime import datetime
from decimal import Decimal
from typing import Any
from uuid import UUID, uuid4

from redis.asyncio import Redis

from app.models.enums import TransportType
from app.services.search.contracts import RouteSearchCriteria
from app.services.search.store.models import (
    MoneySnapshot,
    RouteSegmentSnapshot,
    RouteSnapshot,
)

logger = logging.getLogger(__name__)


class RedisSearchResultsCache:
    def __init__(
        self,
        redis_client: Redis,
        *,
        ttl_seconds: int,
    ) -> None:
        self._redis_client = redis_client
        self._ttl_seconds = ttl_seconds

    async def get(self, criteria: RouteSearchCriteria) -> list[RouteSnapshot] | None:
        cache_key = build_search_cache_key(criteria)
        try:
            raw_value = await self._redis_client.get(cache_key)
        except Exception:
            logger.exception("Search results cache read failed key=%s", cache_key)
            return None

        if raw_value is None:
            return None

        try:
            payload = json.loads(str(raw_value))
            return [_deserialize_route(route) for route in payload["routes"]]
        except Exception:
            logger.exception(
                "Search results cache payload is invalid key=%s",
                cache_key,
            )
            return None

    async def set(
        self,
        criteria: RouteSearchCriteria,
        routes: list[RouteSnapshot],
    ) -> None:
        cache_key = build_search_cache_key(criteria)
        payload = {"routes": [_serialize_route(route) for route in routes]}
        try:
            await self._redis_client.setex(
                cache_key,
                self._ttl_seconds,
                json.dumps(payload, separators=(",", ":"), sort_keys=True),
            )
        except Exception:
            logger.exception("Search results cache write failed key=%s", cache_key)


def build_search_cache_key(criteria: RouteSearchCriteria) -> str:
    payload = {
        "origin_id": str(criteria.origin_id),
        "origin_type": criteria.origin_type.value,
        "destination_id": str(criteria.destination_id),
        "destination_type": criteria.destination_type.value,
        "travel_date": criteria.travel_date.isoformat(),
        "passengers": {
            "adults": criteria.passengers.adults,
            "children": criteria.passengers.children,
            "infants": criteria.passengers.infants,
        },
        "transport_types": [item.value for item in criteria.transport_types],
        "preferences": {
            "sort": criteria.preferences.sort.value,
            "max_transfers": criteria.preferences.max_transfers,
            "max_price": _decimal_to_str(criteria.preferences.max_price),
            "max_duration_minutes": criteria.preferences.max_duration_minutes,
        },
    }
    digest = hashlib.sha256(
        json.dumps(payload, separators=(",", ":"), sort_keys=True).encode("utf-8")
    ).hexdigest()
    return f"search-results:{digest}"


def clone_routes_for_search(
    *,
    search_id: UUID,
    routes: list[RouteSnapshot],
) -> list[RouteSnapshot]:
    return [
        RouteSnapshot(
            route_id=uuid4(),
            search_id=search_id,
            source=route.source,
            segment_ids=route.segment_ids,
            departure_at=route.departure_at,
            arrival_at=route.arrival_at,
            duration_minutes=route.duration_minutes,
            transfers=route.transfers,
            total_price=route.total_price,
            transport_types=route.transport_types,
            segments=route.segments,
            booking_available=route.booking_available,
            refresh_required=route.refresh_required,
        )
        for route in routes
    ]


def _serialize_route(route: RouteSnapshot) -> dict[str, Any]:
    return {
        "route_id": str(route.route_id),
        "search_id": str(route.search_id),
        "source": route.source,
        "segment_ids": [str(segment_id) for segment_id in route.segment_ids],
        "departure_at": route.departure_at.isoformat(),
        "arrival_at": route.arrival_at.isoformat(),
        "duration_minutes": route.duration_minutes,
        "transfers": route.transfers,
        "total_price": _serialize_money(route.total_price),
        "transport_types": [item.value for item in route.transport_types],
        "segments": [_serialize_segment(segment) for segment in route.segments],
        "booking_available": route.booking_available,
        "refresh_required": route.refresh_required,
    }


def _deserialize_route(payload: dict[str, Any]) -> RouteSnapshot:
    return RouteSnapshot(
        route_id=UUID(payload["route_id"]),
        search_id=UUID(payload["search_id"]),
        source=payload["source"],
        segment_ids=tuple(UUID(value) for value in payload["segment_ids"]),
        departure_at=datetime.fromisoformat(payload["departure_at"]),
        arrival_at=datetime.fromisoformat(payload["arrival_at"]),
        duration_minutes=int(payload["duration_minutes"]),
        transfers=int(payload["transfers"]),
        total_price=_deserialize_money(payload["total_price"]),
        transport_types=tuple(
            TransportType(value) for value in payload["transport_types"]
        ),
        segments=tuple(_deserialize_segment(item) for item in payload["segments"]),
        booking_available=bool(payload["booking_available"]),
        refresh_required=bool(payload["refresh_required"]),
    )


def _serialize_segment(segment: RouteSegmentSnapshot) -> dict[str, Any]:
    return {
        "segment_id": str(segment.segment_id),
        "transport_type": segment.transport_type.value,
        "carrier": segment.carrier,
        "carrier_code": segment.carrier_code,
        "segment_code": segment.segment_code,
        "origin_id": str(segment.origin_id),
        "origin_code": segment.origin_code,
        "origin_label": segment.origin_label,
        "destination_id": str(segment.destination_id),
        "destination_code": segment.destination_code,
        "destination_label": segment.destination_label,
        "departure_at": segment.departure_at.isoformat(),
        "arrival_at": segment.arrival_at.isoformat(),
        "duration_minutes": segment.duration_minutes,
        "price": _serialize_money(segment.price),
        "available_seats": segment.available_seats,
        "source_system": segment.source_system,
        "source_record_id": segment.source_record_id,
        "valid_from": segment.valid_from.isoformat(),
        "valid_to": (
            segment.valid_to.isoformat() if segment.valid_to is not None else None
        ),
    }


def _deserialize_segment(payload: dict[str, Any]) -> RouteSegmentSnapshot:
    return RouteSegmentSnapshot(
        segment_id=UUID(payload["segment_id"]),
        transport_type=TransportType(payload["transport_type"]),
        carrier=payload["carrier"],
        carrier_code=payload["carrier_code"],
        segment_code=payload["segment_code"],
        origin_id=UUID(payload["origin_id"]),
        origin_code=payload["origin_code"],
        origin_label=payload["origin_label"],
        destination_id=UUID(payload["destination_id"]),
        destination_code=payload["destination_code"],
        destination_label=payload["destination_label"],
        departure_at=datetime.fromisoformat(payload["departure_at"]),
        arrival_at=datetime.fromisoformat(payload["arrival_at"]),
        duration_minutes=int(payload["duration_minutes"]),
        price=_deserialize_money(payload["price"]),
        available_seats=payload["available_seats"],
        source_system=payload["source_system"],
        source_record_id=payload["source_record_id"],
        valid_from=datetime.fromisoformat(payload["valid_from"]),
        valid_to=(
            datetime.fromisoformat(payload["valid_to"])
            if payload["valid_to"] is not None
            else None
        ),
    )


def _serialize_money(value: MoneySnapshot | None) -> dict[str, str] | None:
    if value is None:
        return None
    return {"amount": str(value.amount), "currency": value.currency}


def _deserialize_money(payload: dict[str, str] | None) -> MoneySnapshot | None:
    if payload is None:
        return None
    return MoneySnapshot(
        amount=Decimal(payload["amount"]),
        currency=payload["currency"],
    )


def _decimal_to_str(value: Decimal | None) -> str | None:
    if value is None:
        return None
    return str(value)


__all__ = [
    "RedisSearchResultsCache",
    "build_search_cache_key",
    "clone_routes_for_search",
]
