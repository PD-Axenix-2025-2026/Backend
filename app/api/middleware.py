from __future__ import annotations

import logging
from collections.abc import Awaitable, Callable
from time import perf_counter
from uuid import uuid4

from fastapi import FastAPI, Request, Response

from app.core.logging import (
    REQUEST_ID_HEADER,
    build_log_extra,
    reset_request_id,
    set_request_id,
)

logger = logging.getLogger("app.http")


def register_request_logging_middleware(app: FastAPI) -> None:
    @app.middleware("http")
    async def request_logging_middleware(
        request: Request,
        call_next: Callable[[Request], Awaitable[Response]],
    ) -> Response:
        request_id = request.headers.get(REQUEST_ID_HEADER) or str(uuid4())
        token = set_request_id(request_id)
        request.state.request_id = request_id
        start_time = perf_counter()
        response: Response | None = None

        try:
            response = await call_next(request)
            response.headers[REQUEST_ID_HEADER] = request_id
            return response
        except Exception:
            logger.exception(
                "Unhandled HTTP request error method=%s path=%s client_ip=%s",
                request.method,
                request.url.path,
                _resolve_client_ip(request),
                extra=build_log_extra(request_id=request_id),
            )
            raise
        finally:
            duration_ms = (perf_counter() - start_time) * 1000
            status_code = 500 if response is None else response.status_code
            logger.info(
                (
                    "HTTP request completed method=%s path=%s status_code=%s "
                    "duration_ms=%.2f client_ip=%s"
                ),
                request.method,
                request.url.path,
                status_code,
                duration_ms,
                _resolve_client_ip(request),
                extra=build_log_extra(request_id=request_id),
            )
            reset_request_id(token)


def _resolve_client_ip(request: Request) -> str:
    if request.client is None:
        return "-"
    return request.client.host
