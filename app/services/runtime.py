from __future__ import annotations

import asyncio
from uuid import UUID

from app.services.models import RouteSearchCriteria, SearchNotFoundError
from app.services.ports import SearchStateStorePort
from app.services.search_service_logging import (
    log_background_search_cancelled,
    log_background_search_completed,
    log_background_search_failed,
    log_background_search_started,
    log_background_search_state_missing,
    log_background_task_started,
    log_shutdown,
)
from app.services.use_cases.searches import RunSearchUseCase


class SearchRuntimeCoordinator:
    def __init__(
        self,
        run_search_use_case: RunSearchUseCase,
        search_state_store: SearchStateStorePort,
    ) -> None:
        self._run_search_use_case = run_search_use_case
        self._search_state_store = search_state_store
        self._tasks: set[asyncio.Task[None]] = set()

    def dispatch(
        self,
        *,
        search_id: UUID,
        criteria: RouteSearchCriteria,
    ) -> None:
        log_background_task_started(search_id)
        task = asyncio.create_task(
            self._run(search_id=search_id, criteria=criteria),
            name=f"route-search-{search_id}",
        )
        self._tasks.add(task)
        task.add_done_callback(self._tasks.discard)

    async def shutdown(self) -> None:
        tasks = tuple(self._tasks)
        log_shutdown(len(tasks))
        for task in tasks:
            task.cancel()
        if tasks:
            await asyncio.gather(*tasks, return_exceptions=True)

    async def _run(
        self,
        *,
        search_id: UUID,
        criteria: RouteSearchCriteria,
    ) -> None:
        log_background_search_started(search_id=search_id, criteria=criteria)
        try:
            routes = await self._run_search_use_case.execute(
                search_id=search_id,
                criteria=criteria,
            )
            log_background_search_completed(
                search_id=search_id,
                route_count=len(routes),
            )
        except asyncio.CancelledError:
            log_background_search_cancelled(search_id)
            raise
        except SearchNotFoundError:
            log_background_search_state_missing(search_id)
        except Exception as exc:
            log_background_search_failed(search_id)
            try:
                await self._search_state_store.mark_failed(
                    search_id=search_id,
                    error_message=str(exc),
                )
            except SearchNotFoundError:
                return


__all__ = ["SearchRuntimeCoordinator"]
