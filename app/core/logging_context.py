from __future__ import annotations

from contextvars import ContextVar, Token
from uuid import UUID

REQUEST_ID_HEADER = "X-Request-ID"
MISSING_LOG_VALUE = "-"

_request_id_context: ContextVar[str] = ContextVar(
    "request_id",
    default=MISSING_LOG_VALUE,
)


def get_request_id() -> str:
    return _request_id_context.get()


def set_request_id(request_id: str) -> Token[str]:
    return _request_id_context.set(normalize_log_value(request_id))


def reset_request_id(token: Token[str]) -> None:
    _request_id_context.reset(token)


def build_log_extra(**values: object) -> dict[str, object]:
    return {
        key: normalize_log_value(value)
        for key, value in values.items()
        if value is not None
    }


def normalize_log_value(value: object) -> str:
    if value in {None, ""}:
        return MISSING_LOG_VALUE
    if isinstance(value, UUID):
        return str(value)
    return str(value)


__all__ = [
    "MISSING_LOG_VALUE",
    "REQUEST_ID_HEADER",
    "build_log_extra",
    "get_request_id",
    "normalize_log_value",
    "reset_request_id",
    "set_request_id",
]
