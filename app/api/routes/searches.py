import logging
from decimal import Decimal
from typing import Annotated, NoReturn
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query, status

from app.api.dependencies import (
    get_create_search_use_case,
    get_search_results_use_case,
)
from app.api.searches_mapping import (
    build_create_search_log_fields,
    build_results_query,
    build_results_request_log_fields,
    build_search_criteria,
)
from app.api.serializers import build_search_results_response
from app.core.logging import build_log_extra
from app.schemas.searches import (
    SearchCreateRequest,
    SearchCreateResponse,
    SearchResultsResponse,
)
from app.services.models import SearchSortOption
from app.services.search_store_models import SearchNotFoundError
from app.services.search_validation import SearchValidationError
from app.services.use_cases import CreateSearchUseCase, GetSearchResultsUseCase

router = APIRouter()
logger = logging.getLogger(__name__)


@router.post(
    "/searches",
    response_model=SearchCreateResponse,
    status_code=status.HTTP_201_CREATED,
)
async def create_search(
    payload: SearchCreateRequest,
    use_case: Annotated[CreateSearchUseCase, Depends(get_create_search_use_case)],
) -> SearchCreateResponse:
    criteria = build_search_criteria(payload)
    logger.info(
        "Search creation requested %s",
        build_create_search_log_fields(criteria),
    )
    try:
        result = await use_case.execute(criteria)
    except SearchValidationError as exc:
        _raise_search_validation_error(exc)

    logger.info(
        "Search created status=%s poll_after_ms=%s",
        result.status.value,
        result.poll_after_ms,
        extra=build_log_extra(search_id=result.search_id),
    )
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
    use_case: Annotated[
        GetSearchResultsUseCase,
        Depends(get_search_results_use_case),
    ],
    last_update: Annotated[int, Query(ge=0)] = 0,
    sort: SearchSortOption | None = None,
    max_price: Annotated[Decimal | None, Query(ge=0)] = None,
    max_transfers: Annotated[int | None, Query(ge=0)] = None,
    max_duration_minutes: Annotated[int | None, Query(ge=0)] = None,
    transport_types: str | None = None,
    limit: Annotated[int, Query(ge=1, le=100)] = 20,
    offset: Annotated[int, Query(ge=0)] = 0,
) -> SearchResultsResponse:
    logger.debug(
        "Search results requested %s",
        build_results_request_log_fields(
            last_update=last_update,
            sort=sort,
            max_price=max_price,
            max_transfers=max_transfers,
            max_duration_minutes=max_duration_minutes,
            transport_types=transport_types,
            limit=limit,
            offset=offset,
        ),
        extra=build_log_extra(search_id=search_id),
    )
    try:
        page = await use_case.execute(
            search_id=search_id,
            query=build_results_query(
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
        _raise_search_not_found(search_id, exc)

    logger.debug(
        "Search results returned status=%s total_found=%s item_count=%s last_update=%s",
        page.status.value,
        page.total_found,
        len(page.items),
        page.last_update,
        extra=build_log_extra(search_id=search_id),
    )
    return build_search_results_response(page)


def _raise_search_validation_error(exc: SearchValidationError) -> NoReturn:
    logger.warning("Search creation rejected reason=%s", str(exc))
    raise HTTPException(status_code=422, detail=str(exc)) from exc


def _raise_search_not_found(
    search_id: UUID,
    exc: SearchNotFoundError,
) -> NoReturn:
    logger.warning(
        "Search results requested for unknown search",
        extra=build_log_extra(search_id=search_id),
    )
    raise HTTPException(status_code=404, detail="Search not found") from exc
