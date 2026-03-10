from dataclasses import dataclass

from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, async_sessionmaker

from app.core.config import Settings
from app.providers.database import DatabaseRouteProvider
from app.repositories.location_repository import LocationRepository
from app.repositories.route_segment_repository import RouteSegmentRepository
from app.services.location_service import LocationService
from app.services.route_aggregation import RouteAggregationService


@dataclass(slots=True)
class AppContainer:
    settings: Settings
    engine: AsyncEngine
    session_factory: async_sessionmaker[AsyncSession]

    def build_location_repository(self, session: AsyncSession) -> LocationRepository:
        return LocationRepository(session)

    def build_route_segment_repository(
        self,
        session: AsyncSession,
    ) -> RouteSegmentRepository:
        return RouteSegmentRepository(session)

    def build_location_service(self, session: AsyncSession) -> LocationService:
        return LocationService(
            location_repository=self.build_location_repository(session),
        )

    def build_route_aggregation_service(
        self,
        session: AsyncSession,
    ) -> RouteAggregationService:
        repository = self.build_route_segment_repository(session)
        provider = DatabaseRouteProvider(repository=repository)
        return RouteAggregationService(providers=[provider])
