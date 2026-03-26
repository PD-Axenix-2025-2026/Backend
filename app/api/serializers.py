from collections.abc import Iterable, Sequence
from decimal import Decimal

from app.schemas.routes import (
    MoneyResponse,
    RouteBookingResponse,
    RouteDetailResponse,
    RouteListItemResponse,
    RouteSegmentResponse,
    RouteSummaryResponse,
)
from app.schemas.searches import (
    DurationRangeResponse,
    PriceRangeResponse,
    SearchResultsFacetsResponse,
    SearchResultsMetaResponse,
    SearchResultsResponse,
    TransferFacetResponse,
    TransportTypeFacetResponse,
)
from app.services.models import (
    DecimalRange,
    MoneySnapshot,
    RouteListView,
    RouteSegmentSnapshot,
    RouteSnapshot,
    SearchResultsPage,
    TransferFacet,
    TransportTypeFacet,
)


def build_money_response(money: MoneySnapshot) -> MoneyResponse:
    return MoneyResponse(amount=float(money.amount), currency=money.currency)


def build_route_summary_response(route: RouteSnapshot) -> RouteSummaryResponse:
    return RouteSummaryResponse(
        departure_at=route.departure_at,
        arrival_at=route.arrival_at,
        duration_minutes=route.duration_minutes,
        transfers=route.transfers,
        total_price=build_money_response(route.total_price),
    )


def build_route_segment_response(
    segment: RouteSegmentSnapshot,
) -> RouteSegmentResponse:
    return RouteSegmentResponse(
        segment_id=segment.segment_id,
        transport_type=segment.transport_type,
        carrier=segment.carrier,
        carrier_code=segment.carrier_code,
        segment_code=segment.segment_code,
        origin_id=segment.origin_id,
        origin_code=segment.origin_code,
        origin_label=segment.origin_label,
        destination_id=segment.destination_id,
        destination_code=segment.destination_code,
        destination_label=segment.destination_label,
        departure_at=segment.departure_at,
        arrival_at=segment.arrival_at,
        duration_minutes=segment.duration_minutes,
        price=build_money_response(segment.price),
        available_seats=segment.available_seats,
        source_system=segment.source_system,
        source_record_id=segment.source_record_id,
        valid_from=segment.valid_from,
        valid_to=segment.valid_to,
    )


def build_route_list_item_response(item: RouteListView) -> RouteListItemResponse:
    route = item.route
    return RouteListItemResponse(
        route_id=route.route_id,
        summary=build_route_summary_response(route),
        segments=_build_segments_response(route.segments),
        labels=list(item.labels),
        booking=_build_route_booking_response(route),
    )


def build_route_detail_response(route: RouteSnapshot) -> RouteDetailResponse:
    return RouteDetailResponse(
        route_id=route.route_id,
        search_id=route.search_id,
        source=route.source,
        segment_ids=list(route.segment_ids),
        summary=build_route_summary_response(route),
        segments=_build_segments_response(route.segments),
        labels=list(route.base_labels),
        booking=_build_route_booking_response(route),
    )


def build_search_results_response(page: SearchResultsPage) -> SearchResultsResponse:
    return SearchResultsResponse(
        search_id=page.search_id,
        status=page.status,
        is_complete=page.is_complete,
        last_update=page.last_update,
        meta=SearchResultsMetaResponse(
            total_found=page.total_found,
            currency=page.currency,
            stale_after_sec=page.stale_after_sec,
        ),
        facets=SearchResultsFacetsResponse(
            transport_types=_build_transport_type_facet_responses(
                page.transport_type_facets
            ),
            transfers=_build_transfer_facet_responses(page.transfer_facets),
            price=_build_price_range_response(page.price_range),
            duration_minutes=DurationRangeResponse(
                min=page.duration_range.min,
                max=page.duration_range.max,
            ),
        ),
        items=[build_route_list_item_response(item) for item in page.items],
        error_message=page.error_message,
    )


def _build_route_booking_response(route: RouteSnapshot) -> RouteBookingResponse:
    return RouteBookingResponse(
        available=route.booking_available,
        refresh_required=route.refresh_required,
    )


def _build_segments_response(
    segments: Sequence[RouteSegmentSnapshot],
) -> list[RouteSegmentResponse]:
    return [build_route_segment_response(segment) for segment in segments]


def _build_transport_type_facet_responses(
    facets: Iterable[TransportTypeFacet],
) -> list[TransportTypeFacetResponse]:
    return [
        TransportTypeFacetResponse(value=facet.value, count=facet.count)
        for facet in facets
    ]


def _build_transfer_facet_responses(
    facets: Iterable[TransferFacet],
) -> list[TransferFacetResponse]:
    return [
        TransferFacetResponse(value=facet.value, count=facet.count) for facet in facets
    ]


def _build_price_range_response(price_range: DecimalRange) -> PriceRangeResponse:
    return PriceRangeResponse(
        min=_decimal_to_float(price_range.min),
        max=_decimal_to_float(price_range.max),
    )


def _decimal_to_float(value: Decimal | None) -> float | None:
    if value is None:
        return None
    return float(value)
