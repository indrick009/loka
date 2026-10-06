"""Structured logging with automatic request-context binding."""

from __future__ import annotations

import logging
import sys
from typing import Any

import structlog
from structlog.typing import FilteringBoundLogger

from loka.shared.application.context import current_context

_configured = False


def _merge_context(_logger: Any, _name: str, event_dict: dict[str, Any]) -> dict[str, Any]:
    for key, value in current_context().items():
        if value and key not in event_dict:
            event_dict[key] = value
    return event_dict


def configure_logging(*, level: str = "INFO", json_output: bool = True) -> None:
    global _configured
    if _configured:
        return

    logging.basicConfig(format="%(message)s", stream=sys.stdout, level=level.upper())
    for noisy in ("uvicorn.access", "sqlalchemy.engine", "aio_pika", "botocore", "asyncio"):
        logging.getLogger(noisy).setLevel(logging.WARNING)

    processors: list[Any] = [
        structlog.contextvars.merge_contextvars,
        structlog.processors.add_log_level,
        structlog.processors.TimeStamper(fmt="iso", utc=True),
        structlog.processors.StackInfoRenderer(),
        structlog.processors.format_exc_info,
        _merge_context,
    ]
    processors.append(
        structlog.processors.JSONRenderer()
        if json_output
        else structlog.dev.ConsoleRenderer(colors=sys.stdout.isatty())
    )

    structlog.configure(
        processors=processors,
        wrapper_class=structlog.make_filtering_bound_logger(
            getattr(logging, level.upper(), logging.INFO)
        ),
        logger_factory=structlog.PrintLoggerFactory(),
        cache_logger_on_first_use=True,
    )
    _configured = True


def get_logger(name: str, **initial: Any) -> FilteringBoundLogger:
    if not _configured:
        configure_logging()
    logger = structlog.get_logger(name)
    bound: FilteringBoundLogger = logger.bind(**initial) if initial else logger
    return bound