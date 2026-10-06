"""Request-scoped context propagated through logs, traces and events."""

from __future__ import annotations

import uuid
from contextvars import ContextVar, Token
from typing import Any

_request_id: ContextVar[str | None] = ContextVar("request_id", default=None)
_correlation_id: ContextVar[str | None] = ContextVar("correlation_id", default=None)
_causation_id: ContextVar[str | None] = ContextVar("causation_id", default=None)
_actor_id: ContextVar[str | None] = ContextVar("actor_id", default=None)
_conversation_id: ContextVar[str | None] = ContextVar("conversation_id", default=None)
_property_id: ContextVar[str | None] = ContextVar("property_id", default=None)


def new_id() -> str:
    return str(uuid.uuid4())


def set_request_id(value: str | None = None) -> Token[str | None]:
    return _request_id.set(value or new_id())


def reset_request_id(token: Token[str | None]) -> None:
    _request_id.reset(token)


def set_correlation_id(value: str | None = None) -> Token[str | None]:
    return _correlation_id.set(value or new_id())


def reset_correlation_id(token: Token[str | None]) -> None:
    _correlation_id.reset(token)


def set_causation_id(value: str | None) -> Token[str | None]:
    return _causation_id.set(value)


def reset_causation_id(token: Token[str | None]) -> None:
    _causation_id.reset(token)


def set_actor_id(value: str | None) -> Token[str | None]:
    return _actor_id.set(value)


def reset_actor_id(token: Token[str | None]) -> None:
    _actor_id.reset(token)


def set_conversation_id(value: str | None) -> Token[str | None]:
    return _conversation_id.set(value)


def reset_conversation_id(token: Token[str | None]) -> None:
    _conversation_id.reset(token)


def set_property_id(value: str | None) -> Token[str | None]:
    return _property_id.set(value)


def reset_property_id(token: Token[str | None]) -> None:
    _property_id.reset(token)


def current_context() -> dict[str, Any]:
    return {
        "request_id": _request_id.get(),
        "correlation_id": _correlation_id.get(),
        "causation_id": _causation_id.get(),
        "actor_id": _actor_id.get(),
        "conversation_id": _conversation_id.get(),
        "property_id": _property_id.get(),
    }


def bind_logger(logger: Any, **extra: Any) -> Any:
    """Bind only the context keys that are actually set."""
    return logger.bind(**{k: v for k, v in {**current_context(), **extra}.items() if v})