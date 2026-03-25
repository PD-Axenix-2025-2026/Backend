from decimal import Decimal
from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query, status

from app.api.dependencies import get_search_service
from app.api.query_parsers import parse_csv_enum_values
from app.api.serializers import build_search_results_response
from app.models.enums import TransportType
from app.schemas.searches import (
    SearchCreateRequest,
    SearchCreateResponse,
    SearchResultsResponse,
)
from app.services.contracts import (
    PassengerCounts,
    RouteSearchCriteria,
    RouteSearchPreferences,
    SearchResultsQuery,
    SearchSortOption,
)
from app.services.search_service import SearchService, SearchValidationError
from app.services.search_store import SearchNotFoundError

router = APIRouter()


def _build_search_criteria(payload: SearchCreateRequest) -> RouteSearchCriteria:
    return RouteSearchCriteria(
        origin_id=payload.origin.id,
        origin_type=payload.origin.type,
        destination_id=payload.destination.id,
        destination_type=payload.destination.type,
        travel_date=payload.date,
        passengers=PassengerCounts(
            adults=payload.passengers.adults,
            children=payload.passengers.children,
            infants=payload.passengers.infants,
        ),
        transport_types=tuple(payload.transport_types),
        preferences=RouteSearchPreferences(
            sort=payload.preferences.sort,
            max_transfers=payload.preferences.max_transfers,
            max_price=payload.preferences.max_price,
            max_duration_minutes=payload.preferences.max_duration_minutes,
        ),
    )


def _build_results_query(
    *,
    last_update: int,
    sort: SearchSortOption | None,
    max_price: Decimal | None,
    max_transfers: int | None,
    max_duration_minutes: int | None,
    transport_types: str | None,
    limit: int,
    offset: int,
) -> SearchResultsQuery:
    return SearchResultsQuery(
        last_update=last_update,
        sort=sort,
        max_price=max_price,
        max_transfers=max_transfers,
        max_duration_minutes=max_duration_minutes,
        transport_types=parse_csv_enum_values(
            transport_types,
            enum_type=TransportType,
            parameter_name="transport_types",
        ),
        limit=limit,
        offset=offset,
    )


@router.post(
    "/searches",
    response_model=SearchCreateResponse,
    status_code=status.HTTP_201_CREATED,
)
async def create_search(
    payload: SearchCreateRequest,
    service: Annotated[SearchService, Depends(get_search_service)],
) -> SearchCreateResponse:
    criteria = _build_search_criteria(payload)
    try:
        result = await service.create_search(criteria)
    except SearchValidationError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc

    return SearchCreateResponse(
        search_id=result.search_id,
        status=result.status,
        results_url=result.results_url,
        poll_after_ms=result.poll_after_ms,
        expires_at=result.expires_at,
    )


@router.get(
    "/searches/{search_id}/results",
    response_model=SearchResultsResponse,
    response_model_exclude_none=True,
)
async def get_search_results(
    search_id: UUID,
    service: Annotated[SearchService, Depends(get_search_service)],
    last_update: Annotated[int, Query(ge=0)] = 0,
    sort: SearchSortOption | None = None,
    max_price: Annotated[Decimal | None, Query(ge=0)] = None,
    max_transfers: Annotated[int | None, Query(ge=0)] = None,
    max_duration_minutes: Annotated[int | None, Query(ge=0)] = None,
    transport_types: str | None = None,
    limit: Annotated[int, Query(ge=1, le=100)] = 20,
    offset: Annotated[int, Query(ge=0)] = 0,
) -> SearchResultsResponse:
    try:
        page = await service.get_results(
            search_id=search_id,
            query=_build_results_query(
                last_update=last_update,
                sort=sort,
                max_price=max_price,
                max_transfers=max_transfers,
                max_duration_minutes=max_duration_minutes,
                transport_types=transport_types,
                limit=limit,
                offset=offset,
            ),
        )
    except SearchNotFoundError as exc:
        raise HTTPException(status_code=404, detail="Search not found") from exc

    return build_search_results_response(page)
