import logging
from dataclasses import dataclass, field

from redis.asyncio import Redis
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, async_sessionmaker

from app.adapters.database_route_search import DatabaseRouteSearchAdapter
from app.adapters.route_search_orchestrator import RouteSearchOrchestrator
from app.adapters.rzd_route_search import RzdRouteSearchAdapter
from app.adapters.sqlalchemy_locations import SqlAlchemyLocationReadAdapter
from app.adapters.sqlalchemy_route_segments import SqlAlchemyRouteSegmentReadAdapter
from app.adapters.yandex_route_search import YandexRaspRouteSearchAdapter
from app.clients.rzd_client_factory import RzdConfig, RzdHttpClientFactory
from app.core.config import Settings
from app.services.application.ports import RouteSearchPort
from app.services.application.runtime import SearchRuntimeCoordinator
from app.services.application.use_cases import (
    CreateCheckoutLinkUseCase,
    CreateSearchUseCase,
    GetRouteDetailUseCase,
    GetSearchResultsUseCase,
    ListLocationsUseCase,
    RunSearchUseCase,
)
from app.services.search.cache import RedisSearchResultsCache
from app.services.search.store.memory import InMemorySearchStore
from app.services.search.validation import SearchCriteriaValidator

logger = logging.getLogger(__name__)


@dataclass(slots=True)
class AppContainer:
    settings: Settings
    engine: AsyncEngine
    session_factory: async_sessionmaker[AsyncSession]
    rzd_http_client_factory: RzdHttpClientFactory
    redis_client: Redis | None = None
    rzd_config: RzdConfig = field(default_factory=RzdConfig)
    search_store: InMemorySearchStore = field(default_factory=InMemorySearchStore)
    search_results_cache: RedisSearchResultsCache | None = field(init=False)
    location_reader: SqlAlchemyLocationReadAdapter = field(init=False)
    route_segment_reader: SqlAlchemyRouteSegmentReadAdapter = field(init=False)
    route_search: RouteSearchOrchestrator = field(init=False)
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
        self.search_results_cache = (
            RedisSearchResultsCache(
                self.redis_client,
                ttl_seconds=self.settings.search_cache_ttl_seconds,
            )
            if self.redis_client is not None
            else None
        )

        used_adapters: list[RouteSearchPort] = []

        # если обращаемся к РЖД
        if self.settings.use_rzd_api:
            used_adapters.append(
                RzdRouteSearchAdapter(
                    http_client_factory=self.rzd_http_client_factory,
                    database_session_factory=self.session_factory,
                    config=self.rzd_config,
                )
            )

        # если обращаемся к Яндекс.Расписаниям
        if self.settings.use_yandex_api and self.settings.yandex_rasp_api_key:
            used_adapters.append(
                YandexRaspRouteSearchAdapter(
                    api_key=self.settings.yandex_rasp_api_key,
                    database_session_factory=self.session_factory,
                )
            )

        # Always include database adapter as a fallback source of routes
        # so that local seeded data is considered even when external adapters
        # (RZD / Yandex) are enabled. Database adapter is appended last so
        # external providers can still take precedence when appropriate.
        used_adapters.append(DatabaseRouteSearchAdapter(self.session_factory))

        self.route_search = RouteSearchOrchestrator(adapters=used_adapters)

        validator = SearchCriteriaValidator(location_reader=self.location_reader)
        run_search_use_case = RunSearchUseCase(
            route_search_port=self.route_search,
            route_segment_reader=self.route_segment_reader,
            search_state_store=self.search_store,
            results_cache=self.search_results_cache,
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
            results_cache=self.search_results_cache,
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
