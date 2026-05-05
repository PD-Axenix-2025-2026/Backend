import asyncio
import logging
from collections.abc import Iterable, Sequence
from typing import cast

from app.services.models import RouteCandidate, RouteSearchCriteria
from app.services.ports import RouteSearchPort

logger = logging.getLogger(__name__)


class RouteSearchOrchestratorError(Exception):
    """Ошибка оркестратора поиска маршрутов."""

    pass


class RouteSearchOrchestrator(RouteSearchPort):
    """
    Оркестратор для запуска нескольких адаптеров поиска маршрутов.

    Можно запускать все адаптеры вместе, либо выбрать подмножество
    через аргумент `adapters` в методе search().
    """

    def __init__(self, adapters: Sequence[RouteSearchPort]):
        self._adapters = list(adapters)

    async def search(
        self,
        criteria: RouteSearchCriteria,
        adapters: Iterable[RouteSearchPort] | None = None,
    ) -> list[RouteCandidate]:
        """
        Запускает поиск по всем адаптерам (или выбранным) параллельно
        и возвращает объединённый список результатов.

        ВНИМАНИЕ: сейчас результаты просто конкатенируются в один список.
        Позже нужно будет реализовать полноценный мерж.
        """
        selected_adapters = list(adapters) if adapters is not None else self._adapters

        if not selected_adapters:
            raise Exception("No adapters passed to route search orchestrator")

        tasks = [adapter.search(criteria) for adapter in selected_adapters]
        results = await asyncio.gather(*tasks, return_exceptions=True)

        combined: list[RouteCandidate] = []
        errors: list[Exception] = []

        for adapter, result in zip(selected_adapters, results, strict=True):
            if isinstance(result, Exception):
                logger.error(
                    "Route search adapter failed: %s, error=%s",
                    adapter.__class__.__name__,
                    result,
                )
                errors.append(result)
            else:
                # TODO: Merge results properly between sources.
                combined.extend(cast(list[RouteCandidate], result))

        if not combined and errors:
            raise RouteSearchOrchestratorError("All route search adapters failed")

        return combined
