import logging
from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException

from app.api.dependencies import (
    get_create_checkout_link_use_case,
    get_route_detail_use_case,
)
from app.api.serializers import build_route_detail_response
from app.core.logging import build_log_extra
from app.schemas.routes import (
    CheckoutLinkRequest,
    CheckoutLinkResponse,
    RouteDetailResponse,
)
from app.services.search_store_models import RouteNotFoundError
from app.services.use_cases import CreateCheckoutLinkUseCase, GetRouteDetailUseCase

router = APIRouter()
logger = logging.getLogger(__name__)


@router.get("/routes/{route_id}", response_model=RouteDetailResponse)
async def get_route_detail(
    route_id: UUID,
    use_case: Annotated[GetRouteDetailUseCase, Depends(get_route_detail_use_case)],
) -> RouteDetailResponse:
    logger.debug(
        "Route detail requested",
        extra=build_log_extra(route_id=route_id),
    )
    try:
        route = await use_case.execute(route_id)
    except RouteNotFoundError as exc:
        logger.warning(
            "Route detail requested for unknown route",
            extra=build_log_extra(route_id=route_id),
        )
        raise HTTPException(status_code=404, detail="Route not found") from exc

    logger.debug(
        "Route detail returned search_id=%s segment_count=%s",
        route.search_id,
        len(route.segments),
        extra=build_log_extra(search_id=route.search_id, route_id=route_id),
    )
    return build_route_detail_response(route)


@router.post(
    "/routes/{route_id}/checkout-link",
    response_model=CheckoutLinkResponse,
    response_model_exclude_none=True,
)
async def create_checkout_link(
    route_id: UUID,
    use_case: Annotated[
        CreateCheckoutLinkUseCase,
        Depends(get_create_checkout_link_use_case),
    ],
    payload: CheckoutLinkRequest | None = None,
) -> CheckoutLinkResponse:
    provider_offer_id = None if payload is None else payload.provider_offer_id
    logger.info(
        "Checkout link requested provider_offer_present=%s",
        provider_offer_id is not None,
        extra=build_log_extra(route_id=route_id),
    )

    try:
        checkout_link = await use_case.execute(
            route_id=route_id,
            provider_offer_id=provider_offer_id,
        )
    except RouteNotFoundError as exc:
        logger.warning(
            "Checkout link requested for unknown route",
            extra=build_log_extra(route_id=route_id),
        )
        raise HTTPException(status_code=404, detail="Route not found") from exc

    logger.info(
        "Checkout link created",
        extra=build_log_extra(route_id=route_id),
    )
    return CheckoutLinkResponse(
        method=checkout_link.method,
        url=checkout_link.url,
        expires_at=checkout_link.expires_at,
        params=checkout_link.params,
    )
