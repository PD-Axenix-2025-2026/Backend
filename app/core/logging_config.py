from __future__ import annotations

import logging
from logging.config import dictConfig

from app.core.config import Settings
from app.core.logging_context import (
    MISSING_LOG_VALUE,
    get_request_id,
    normalize_log_value,
)


class RequestContextFilter(logging.Filter):
    def filter(self, record: logging.LogRecord) -> bool:
        record.request_id = normalize_log_value(
            getattr(record, "request_id", get_request_id())
        )
        record.search_id = normalize_log_value(
            getattr(record, "search_id", MISSING_LOG_VALUE)
        )
        record.route_id = normalize_log_value(
            getattr(record, "route_id", MISSING_LOG_VALUE)
        )
        return True


def configure_logging(settings: Settings) -> None:
    log_level = settings.log_level or ("DEBUG" if settings.debug else "INFO")
    dictConfig(
        {
            "version": 1,
            "disable_existing_loggers": False,
            "filters": {
                "request_context": {
                    "()": RequestContextFilter,
                }
            },
            "formatters": {
                "default": {
                    "format": (
                        "%(asctime)s %(levelname)s %(name)s "
                        "request_id=%(request_id)s search_id=%(search_id)s "
                        "route_id=%(route_id)s %(message)s"
                    )
                }
            },
            "handlers": {
                "console": {
                    "class": "logging.StreamHandler",
                    "formatter": "default",
                    "filters": ["request_context"],
                }
            },
            "root": {
                "level": log_level,
                "handlers": ["console"],
            },
            "loggers": {
                "uvicorn.access": {
                    "level": "WARNING",
                    "handlers": ["console"],
                    "propagate": False,
                }
            },
        }
    )


__all__ = ["RequestContextFilter", "configure_logging"]
