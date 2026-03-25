from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException

from app.api.dependencies import get_search_service
from app.api.serializers import build_route_detail_response
from app.schemas.routes import (
    CheckoutLinkRequest,
    CheckoutLinkResponse,
    RouteDetailResponse,
)
from app.services.search_service import SearchService
from app.services.search_store import RouteNotFoundError

router = APIRouter()


@router.get("/routes/{route_id}", response_model=RouteDetailResponse)
async def get_route_detail(
    route_id: UUID,
    service: Annotated[SearchService, Depends(get_search_service)],
) -> RouteDetailResponse:
    try:
        route = await service.get_route_detail(route_id)
    except RouteNotFoundError as exc:
        raise HTTPException(status_code=404, detail="Route not found") from exc

    return build_route_detail_response(route)


@router.post(
    "/routes/{route_id}/checkout-link",
    response_model=CheckoutLinkResponse,
    response_model_exclude_none=True,
)
async def create_checkout_link(
    route_id: UUID,
    service: Annotated[SearchService, Depends(get_search_service)],
    payload: CheckoutLinkRequest | None = None,
) -> CheckoutLinkResponse:
    provider_offer_id = None if payload is None else payload.provider_offer_id

    try:
        checkout_link = await service.build_checkout_link(
            route_id=route_id,
            provider_offer_id=provider_offer_id,
        )
    except RouteNotFoundError as exc:
        raise HTTPException(status_code=404, detail="Route not found") from exc

    return CheckoutLinkResponse(
        method=checkout_link.method,
        url=checkout_link.url,
        expires_at=checkout_link.expires_at,
        params=checkout_link.params,
    )
