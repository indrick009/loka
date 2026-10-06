"""Translation of domain failures into HTTP responses.

Without this, every business rule violation reaches the catch-all handler and
leaks as a 500, which tells the caller nothing and pollutes error dashboards.
The body is the domain error itself, so the stable ``code`` is what clients
should branch on.
"""

from __future__ import annotations

from typing import Any

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse

from loka.interfaces.http.auth import BEARER_CHALLENGE
from loka.shared.domain.errors import (
    AuthorizationDenied,
    ConcurrencyConflict,
    DomainError,
    ExternalServiceUnavailable,
    RateLimited,
    ResourceNotFound,
    Unauthenticated,
)

_STATUS_BY_ERROR: tuple[tuple[type[DomainError], int], ...] = (
    (Unauthenticated, 401),
    (ResourceNotFound, 404),
    (AuthorizationDenied, 403),
    (ConcurrencyConflict, 409),
    (RateLimited, 429),
    (ExternalServiceUnavailable, 503),
)


def register_error_handlers(app: FastAPI, *, logger: Any) -> None:
    @app.exception_handler(DomainError)
    async def domain_error(_: Request, exc: DomainError) -> JSONResponse:
        status = _status_for(exc)
        event = {
            "warning": "domain_error_rejected",
            "error": "domain_error_unavailable",
        }["error" if status >= 500 else "warning"]
        extra: dict[str, Any] = {"code": exc.code, "message": exc.message}
        extra.update(exc.context)
        getattr(logger, "error" if status >= 500 else "warning")(event, **extra)
        response = JSONResponse(status_code=status, content=exc.to_dict())
        if isinstance(exc, Unauthenticated):
            response.headers["www-authenticate"] = BEARER_CHALLENGE
        return response


def _status_for(exc: DomainError) -> int:
    for error_type, status in _STATUS_BY_ERROR:
        if isinstance(exc, error_type):
            return status
    return 422
