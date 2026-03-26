from __future__ import annotations

import logging
from typing import NoReturn

from app.models.location import Location
from app.services.contracts import RouteSearchCriteria
from app.services.ports import LocationReadPort

logger = logging.getLogger("app.services.search_service")


class SearchValidationError(ValueError):
    pass


class SearchCriteriaValidator:
    def __init__(
        self,
        location_reader: LocationReadPort,
    ) -> None:
        self._location_reader = location_reader

    async def validate(self, criteria: RouteSearchCriteria) -> None:
        self._validate_location_pair(criteria)
        self._validate_passengers(criteria)

        logger.debug(
            "Validating search criteria origin_id=%s destination_id=%s travel_date=%s",
            criteria.origin_id,
            criteria.destination_id,
            criteria.travel_date,
        )
        origin, destination = await self._load_selected_locations(criteria)
        self._validate_selected_locations(criteria, origin, destination)
        logger.debug("Search validation completed successfully")

    async def _load_selected_locations(
        self,
        criteria: RouteSearchCriteria,
    ) -> tuple[Location | None, Location | None]:
        origin = await self._location_reader.get_by_id(criteria.origin_id)
        destination = await self._location_reader.get_by_id(criteria.destination_id)
        return origin, destination

    def _validate_location_pair(self, criteria: RouteSearchCriteria) -> None:
        if criteria.origin_id == criteria.destination_id:
            self._raise_validation_error(
                "Origin and destination must be different",
                "Search validation failed because origin and destination are equal "
                "origin_id=%s",
                criteria.origin_id,
            )

    def _validate_passengers(self, criteria: RouteSearchCriteria) -> None:
        if criteria.passengers.adults < 1:
            self._raise_validation_error(
                "At least one adult passenger is required",
                "Search validation failed because adult passenger count is invalid "
                "adults=%s",
                criteria.passengers.adults,
            )
        if criteria.passengers.total <= 0:
            self._raise_validation_error(
                "At least one passenger is required",
                "Search validation failed because passenger count is invalid total=%s",
                criteria.passengers.total,
            )

    def _validate_selected_locations(
        self,
        criteria: RouteSearchCriteria,
        origin: Location | None,
        destination: Location | None,
    ) -> None:
        if origin is None or destination is None:
            self._raise_validation_error(
                "Selected locations were not found",
                "Search validation failed because selected locations were not found "
                "origin_id=%s destination_id=%s",
                criteria.origin_id,
                criteria.destination_id,
            )
        if origin.location_type != criteria.origin_type:
            self._raise_validation_error(
                "Origin location type does not match",
                "Search validation failed because origin type does not match "
                "origin_id=%s expected=%s actual=%s",
                criteria.origin_id,
                criteria.origin_type.value,
                origin.location_type.value,
            )
        if destination.location_type != criteria.destination_type:
            self._raise_validation_error(
                "Destination location type does not match",
                "Search validation failed because destination type does not match "
                "destination_id=%s expected=%s actual=%s",
                criteria.destination_id,
                criteria.destination_type.value,
                destination.location_type.value,
            )

    def _raise_validation_error(
        self,
        error_message: str,
        log_message: str,
        *log_args: object,
    ) -> NoReturn:
        logger.warning(log_message, *log_args)
        raise SearchValidationError(error_message)


__all__ = [
    "SearchCriteriaValidator",
    "SearchValidationError",
]
