from abc import ABC, abstractmethod

from app.services.contracts import RouteCandidate, RouteSearchCriteria


class RouteProvider(ABC):
    @abstractmethod
    async def search_routes(
        self,
        criteria: RouteSearchCriteria,
    ) -> list[RouteCandidate]:
        raise NotImplementedError
