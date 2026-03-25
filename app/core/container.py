from dataclasses import dataclass, field

from redis.asyncio import Redis
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, async_sessionmaker

from app.core.config import Settings
from app.providers.database import DatabaseRouteProvider
from app.repositories.location_repository import LocationRepository
from app.repositories.route_segment_repository import RouteSegmentRepository
from app.services.location_service import LocationService
from app.services.route_aggregation import RouteAggregationService
from app.services.search_service import SearchService
from app.services.search_store import InMemorySearchStore


@dataclass(slots=True)
class AppContainer:
    settings: Settings
    engine: AsyncEngine
    session_factory: async_sessionmaker[AsyncSession]
    redis_client: Redis | None = None
    search_store: InMemorySearchStore = field(default_factory=InMemorySearchStore)
    search_service: SearchService = field(init=False)

    def __post_init__(self) -> None:
        self.search_service = SearchService(
            settings=self.settings,
            session_factory=self.session_factory,
            search_store=self.search_store,
            location_repository_factory=self.build_location_repository,
            route_segment_repository_factory=self.build_route_segment_repository,
            route_aggregation_factory=self.build_route_aggregation_service,
        )

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

    async def shutdown(self) -> None:
        await self.search_service.shutdown()
