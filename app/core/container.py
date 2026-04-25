import logging
from dataclasses import dataclass, field
from uuid import UUID

from redis.asyncio import Redis
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, async_sessionmaker

# from app.adapters.database_route_search import DatabaseRouteSearchAdapter
from app.adapters.rzd_route_search import RzdRouteSearchAdapter
from app.adapters.sqlalchemy_locations import SqlAlchemyLocationReadAdapter
from app.adapters.sqlalchemy_route_segments import SqlAlchemyRouteSegmentReadAdapter
from app.clients.rzd_client_factory import RzdConfig, RzdHttpClientFactory
from app.core.config import Settings
from app.services.runtime import SearchRuntimeCoordinator
from app.services.search_store import InMemorySearchStore
from app.services.search_validation import SearchCriteriaValidator
from app.services.use_cases import (
    CreateCheckoutLinkUseCase,
    CreateSearchUseCase,
    GetRouteDetailUseCase,
    GetSearchResultsUseCase,
    ListLocationsUseCase,
    RunSearchUseCase,
)

logger = logging.getLogger(__name__)


@dataclass(slots=True)
class AppContainer:
    settings: Settings
    engine: AsyncEngine
    session_factory: async_sessionmaker[AsyncSession]
    rzd_http_client_factory: RzdHttpClientFactory
    redis_client: Redis | None = None
    rzd_config: RzdConfig = field(default_factory=RzdConfig)
    station_code_mapping: dict[UUID, str] = field(default_factory=dict)
    search_store: InMemorySearchStore = field(default_factory=InMemorySearchStore)
    location_reader: SqlAlchemyLocationReadAdapter = field(init=False)
    route_segment_reader: SqlAlchemyRouteSegmentReadAdapter = field(init=False)
    # route_search: DatabaseRouteSearchAdapter = field(init=False)
    route_search: RzdRouteSearchAdapter = field(init=False)
    list_locations_use_case: ListLocationsUseCase = field(init=False)
    create_search_use_case: CreateSearchUseCase = field(init=False)
    get_search_results_use_case: GetSearchResultsUseCase = field(init=False)
    get_route_detail_use_case: GetRouteDetailUseCase = field(init=False)
    create_checkout_link_use_case: CreateCheckoutLinkUseCase = field(init=False)
    search_runtime_coordinator: SearchRuntimeCoordinator = field(init=False)

    def __post_init__(self) -> None:
        logger.debug("Initializing application container")
        self.location_reader = SqlAlchemyLocationReadAdapter(self.session_factory)
        self.route_segment_reader = SqlAlchemyRouteSegmentReadAdapter(
            self.session_factory
        )
        # self.route_search = DatabaseRouteSearchAdapter(self.session_factory)

        self.route_search = RzdRouteSearchAdapter(
            station_code_mapping=self.station_code_mapping,
            http_client_factory=self.rzd_http_client_factory,
            config=self.rzd_config,
        )

        validator = SearchCriteriaValidator(location_reader=self.location_reader)
        run_search_use_case = RunSearchUseCase(
            route_search_port=self.route_search,
            route_segment_reader=self.route_segment_reader,
            search_state_store=self.search_store,
        )
        self.search_runtime_coordinator = SearchRuntimeCoordinator(
            run_search_use_case=run_search_use_case,
            search_state_store=self.search_store,
        )
        self.list_locations_use_case = ListLocationsUseCase(
            location_reader=self.location_reader,
        )
        self.create_search_use_case = CreateSearchUseCase(
            settings=self.settings,
            validator=validator,
            search_state_store=self.search_store,
            runtime_coordinator=self.search_runtime_coordinator,
        )
        self.get_search_results_use_case = GetSearchResultsUseCase(
            search_state_store=self.search_store,
        )
        self.get_route_detail_use_case = GetRouteDetailUseCase(
            search_state_store=self.search_store,
        )
        self.create_checkout_link_use_case = CreateCheckoutLinkUseCase(
            settings=self.settings,
            search_state_store=self.search_store,
        )

    async def shutdown(self) -> None:
        logger.info("Shutting down application container")
        await self.search_runtime_coordinator.shutdown()
