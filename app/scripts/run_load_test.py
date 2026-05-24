from __future__ import annotations

import argparse
import asyncio
import sys
import random
import statistics
import time
from collections import defaultdict
from dataclasses import dataclass, field
from datetime import date, timedelta
from typing import Any

import httpx

from app.core.config import get_settings
from app.models.enums import LocationType, TransportType


@dataclass(slots=True, frozen=True)
class LoadTestConfig:
    base_url: str
    users: int
    iterations_per_user: int
    base_date: date
    travel_days: int
    origin_prefix: str
    destination_prefix: str
    search_limit: int
    results_limit: int
    poll_attempts: int
    poll_interval_ms: int
    request_timeout_seconds: float
    include_checkout: bool
    transport_types: tuple[TransportType, ...]
    sort: str
    max_transfers: int


@dataclass(slots=True)
class RequestStats:
    durations_ms: list[float] = field(default_factory=list)
    failures: int = 0

    def add_success(self, duration_ms: float) -> None:
        self.durations_ms.append(duration_ms)

    def add_failure(self) -> None:
        self.failures += 1


@dataclass(slots=True)
class LoadTestSummary:
    metrics: dict[str, RequestStats]
    total_scenarios: int
    total_failures: int


async def main() -> None:
    args = _parse_args()
    config = LoadTestConfig(
        base_url=args.base_url,
        users=args.users,
        iterations_per_user=args.iterations_per_user,
        base_date=args.base_date,
        travel_days=args.travel_days,
        origin_prefix=args.origin_prefix,
        destination_prefix=args.destination_prefix,
        search_limit=args.search_limit,
        results_limit=args.results_limit,
        poll_attempts=args.poll_attempts,
        poll_interval_ms=args.poll_interval_ms,
        request_timeout_seconds=args.request_timeout_seconds,
        include_checkout=args.include_checkout,
        transport_types=tuple(_parse_transport_types(args.transport_types)),
        sort=args.sort,
        max_transfers=args.max_transfers,
    )
    try:
        summary = await run_load_test(config)
    except httpx.ConnectError as exc:
        _print_connection_error(config.base_url)
        raise SystemExit(1) from exc
    except httpx.TimeoutException as exc:
        print(
            f"Load test failed: request timed out while connecting to {config.base_url}",
            file=sys.stderr,
        )
        raise SystemExit(1) from exc

    _print_summary(summary)


async def run_load_test(config: LoadTestConfig) -> LoadTestSummary:
    metrics: dict[str, RequestStats] = defaultdict(RequestStats)
    timeout = httpx.Timeout(config.request_timeout_seconds)
    limits = httpx.Limits(max_connections=config.users * 4, max_keepalive_connections=config.users * 2)

    async with httpx.AsyncClient(
        base_url=config.base_url,
        timeout=timeout,
        limits=limits,
    ) as client:
        await _check_backend_ready(client)
        origin_location, destination_location = await _resolve_locations(client, config)
        total_scenarios = config.users * config.iterations_per_user
        progress_queue: asyncio.Queue[None] = asyncio.Queue()
        progress_task = asyncio.create_task(_progress_printer(progress_queue, total_scenarios))
        tasks = [
            asyncio.create_task(
                _run_user(
                    client=client,
                    config=config,
                    metrics=metrics,
                    origin_location=origin_location,
                    destination_location=destination_location,
                    user_index=user_index,
                    progress_queue=progress_queue,
                )
            )
            for user_index in range(config.users)
        ]
        results = await asyncio.gather(*tasks, return_exceptions=True)
        # wait until progress consumer has processed all scenario completions
        await progress_queue.join()
        progress_task.cancel()

    total_failures = sum(stat.failures for stat in metrics.values())
    for result in results:
        if isinstance(result, Exception):
            total_failures += 1

    return LoadTestSummary(
        metrics=metrics,
        total_scenarios=config.users * config.iterations_per_user,
        total_failures=total_failures,
    )


async def _run_user(
    *,
    client: httpx.AsyncClient,
    config: LoadTestConfig,
    metrics: dict[str, RequestStats],
    origin_location: dict[str, Any],
    destination_location: dict[str, Any],
    user_index: int,
    progress_queue: asyncio.Queue[None],
) -> None:
    rng = random.Random(20260522 + user_index)
    for iteration_index in range(config.iterations_per_user):
        travel_date = config.base_date + timedelta(
            days=rng.randrange(max(config.travel_days, 1))
        )
        await _run_search_flow(
            client=client,
            config=config,
            metrics=metrics,
            origin_location=origin_location,
            destination_location=destination_location,
            travel_date=travel_date,
            user_index=user_index,
            iteration_index=iteration_index,
            progress_queue=progress_queue,
        )


async def _progress_printer(queue: asyncio.Queue[None], total: int) -> None:
    """Consume completion signals and print an updating one-line progress indicator."""
    completed = 0
    try:
        while completed < total:
            await queue.get()
            completed += 1
            queue.task_done()
            # carriage return to overwrite the same line for visibility
            sys.stdout.write(f"\rScenario {completed}/{total}")
            sys.stdout.flush()
    except asyncio.CancelledError:
        return
    finally:
        # ensure we end with a newline when done
        print()


async def _run_search_flow(
    *,
    client: httpx.AsyncClient,
    config: LoadTestConfig,
    metrics: dict[str, RequestStats],
    origin_location: dict[str, Any],
    destination_location: dict[str, Any],
    travel_date: date,
    user_index: int,
    iteration_index: int,
    progress_queue: asyncio.Queue[None],
) -> None:
    payload = {
        "origin": {
            "id": origin_location["id"],
            "type": origin_location["type"],
        },
        "destination": {
            "id": destination_location["id"],
            "type": destination_location["type"],
        },
        "date": travel_date.isoformat(),
        "passengers": {"adults": 1, "children": 0, "infants": 0},
        "transport_types": [transport_type.value for transport_type in config.transport_types],
        "preferences": {
            "sort": config.sort,
            "max_transfers": config.max_transfers,
        },
    }

    create_response = await _timed_request(
        metrics,
        "create_search",
        client.post,
        "/api/searches",
        json=payload,
    )
    create_response.raise_for_status()
    search_id = create_response.json()["search_id"]
    poll_after_ms = int(create_response.json().get("poll_after_ms", config.poll_interval_ms))

    route_id: str | None = None
    for _ in range(config.poll_attempts):
        results_response = await _timed_request(
            metrics,
            "poll_results",
            client.get,
            f"/api/searches/{search_id}/results",
            params={
                "last_update": 0,
                "sort": config.sort,
                "transport_types": ",".join(
                    transport_type.value for transport_type in config.transport_types
                ),
                "limit": config.results_limit,
                "offset": 0,
                "max_transfers": config.max_transfers,
            },
        )
        results_response.raise_for_status()
        results_body = results_response.json()
        items = results_body.get("items", [])
        if results_body.get("is_complete") and items:
            route_id = items[0]["route_id"]
            break
        await asyncio.sleep(poll_after_ms / 1000)

    if route_id is None:
        raise RuntimeError(
            "Search did not complete after "
            f"{config.poll_attempts} polls for user={user_index} iteration={iteration_index}"
        )

    detail_response = await _timed_request(
        metrics,
        "route_detail",
        client.get,
        f"/api/routes/{route_id}",
    )
    detail_response.raise_for_status()

    if config.include_checkout:
        checkout_response = await _timed_request(
            metrics,
            "checkout_link",
            client.post,
            f"/api/routes/{route_id}/checkout-link",
            json={"provider_offer_id": None},
        )
        checkout_response.raise_for_status()
    # signal scenario completion for progress monitoring
    try:
        progress_queue.put_nowait(None)
    except Exception:
        pass

async def _resolve_locations(
    client: httpx.AsyncClient,
    config: LoadTestConfig,
) -> tuple[dict[str, Any], dict[str, Any]]:
    origin_response = await _timed_request(
        defaultdict(RequestStats),
        "location_lookup",
        client.get,
        "/api/locations",
        params={
            "prefix": config.origin_prefix,
            "types": "city",
            "limit": config.search_limit,
        },
    )
    origin_response.raise_for_status()

    destination_response = await _timed_request(
        defaultdict(RequestStats),
        "location_lookup",
        client.get,
        "/api/locations",
        params={
            "prefix": config.destination_prefix,
            "types": "city",
            "limit": config.search_limit,
        },
    )
    destination_response.raise_for_status()

    origin_location = _pick_location_item(origin_response.json().get("items", []), "MOW")
    destination_location = _pick_location_item(destination_response.json().get("items", []), "SPB")
    return origin_location, destination_location


def _pick_location_item(items: list[dict[str, Any]], preferred_code: str) -> dict[str, Any]:
    for item in items:
        if item.get("code") == preferred_code:
            return item
    if items:
        return items[0]
    raise RuntimeError(f"Location autocomplete returned no items for {preferred_code}")


async def _check_backend_ready(client: httpx.AsyncClient) -> None:
    response = await client.get("/api/health")
    response.raise_for_status()


def _print_connection_error(base_url: str) -> None:
    print(
        "Load test failed: backend API is not reachable at "
        f"{base_url}. Start the backend first, for example:\n"
        "  Set-Location \"c:\\Users\\APETROSIA_PC\\Desktop\\progr\\ПД\\Backend\"\n"
        "  poetry run uvicorn app.main:app --reload\n"
        "If you are using Docker, make sure the backend container is running "
        "and that the URL matches the exposed port.",
        file=sys.stderr,
    )


async def _timed_request(
    metrics: dict[str, RequestStats],
    metric_name: str,
    request_callable: Any,
    *args: Any,
    **kwargs: Any,
) -> httpx.Response:
    started_at = time.perf_counter()
    try:
        response = await request_callable(*args, **kwargs)
    except Exception:
        metrics[metric_name].add_failure()
        raise
    duration_ms = (time.perf_counter() - started_at) * 1000
    if response.status_code >= 400:
        metrics[metric_name].add_failure()
    else:
        metrics[metric_name].add_success(duration_ms)
    return response


def _parse_transport_types(raw_value: str) -> list[TransportType]:
    values = [value.strip() for value in raw_value.split(",") if value.strip()]
    if not values:
        return [TransportType.plane, TransportType.train, TransportType.bus]
    return [TransportType(value) for value in values]


def _parse_args() -> argparse.Namespace:
    settings = get_settings()
    parser = argparse.ArgumentParser(
        description=(
            "Run an HTTP load test against the search API using synthetic route data."
        ),
    )
    parser.add_argument(
        "--base-url",
        default="http://127.0.0.1:8000",
        help="Base URL of the site or backend API to test.",
    )
    parser.add_argument("--users", type=int, default=20, help="Concurrent virtual users.")
    parser.add_argument(
        "--iterations-per-user",
        type=int,
        default=10,
        help="How many search flows each virtual user should execute.",
    )
    parser.add_argument(
        "--base-date",
        type=date.fromisoformat,
        default=date(2026, 5, 22),
        help="Start date for generated search requests.",
    )
    parser.add_argument(
        "--travel-days",
        type=int,
        default=30,
        help="How many seeded days to sample while generating search requests.",
    )
    parser.add_argument(
        "--origin-prefix",
        default="Москва",
        help="Autocomplete prefix for the origin location.",
    )
    parser.add_argument(
        "--destination-prefix",
        default="Санкт-Петербург",
        help="Autocomplete prefix for the destination location.",
    )
    parser.add_argument(
        "--search-limit",
        type=int,
        default=10,
        help="Location autocomplete limit when resolving search endpoints.",
    )
    parser.add_argument(
        "--results-limit",
        type=int,
        default=10,
        help="Search results page size.",
    )
    parser.add_argument(
        "--poll-attempts",
        type=int,
        default=30,
        help=(
            "Maximum number of results polls per search. "
            "Defaults to 30 so the test waits longer for background search completion."
        ),
    )
    parser.add_argument(
        "--poll-interval-ms",
        type=int,
        default=settings.search_poll_after_ms,
        help="Sleep between polling requests when a search is still running.",
    )
    parser.add_argument(
        "--request-timeout-seconds",
        type=float,
        default=30.0,
        help="Timeout for each HTTP request.",
    )
    parser.add_argument(
        "--transport-types",
        default="plane,train,bus",
        help="Comma-separated transport types to include in search requests.",
    )
    parser.add_argument(
        "--sort",
        default="best",
        choices=("best", "price", "duration"),
        help="Sort order for search results.",
    )
    parser.add_argument(
        "--max-transfers",
        type=int,
        default=1,
        help="Maximum allowed transfers in search requests.",
    )
    parser.add_argument(
        "--include-checkout",
        action="store_true",
        help="Also call the checkout-link endpoint for the first route.",
    )
    return parser.parse_args()


def _print_summary(summary: LoadTestSummary) -> None:
    print(
        "Load test finished: "
        f"{summary.total_scenarios} scenarios, "
        f"{summary.total_failures} failures."
    )
    for metric_name, stat in sorted(summary.metrics.items()):
        if metric_name == "scenario":
            continue
        if not stat.durations_ms:
            print(f"- {metric_name}: no successful samples, failures={stat.failures}")
            continue
        print(
            f"- {metric_name}: count={len(stat.durations_ms)} "
            f"failures={stat.failures} "
            f"avg={statistics.fmean(stat.durations_ms):.1f}ms "
            f"p95={_percentile(stat.durations_ms, 95):.1f}ms "
            f"max={max(stat.durations_ms):.1f}ms"
        )


def _percentile(values: list[float], percentile: float) -> float:
    ordered = sorted(values)
    if len(ordered) == 1:
        return ordered[0]
    rank = (len(ordered) - 1) * (percentile / 100)
    lower = int(rank)
    upper = min(lower + 1, len(ordered) - 1)
    fraction = rank - lower
    return ordered[lower] + (ordered[upper] - ordered[lower]) * fraction


if __name__ == "__main__":
    asyncio.run(main())