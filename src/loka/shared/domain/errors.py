"""Typed domain errors shared by every bounded context."""

from __future__ import annotations

from typing import Any


class DomainError(Exception):
    """Base class for every business rule violation."""

    code: str = "domain_error"

    def __init__(self, message: str, *, context: dict[str, Any] | None = None) -> None:
        super().__init__(message)
        self.message = message
        self.context = context or {}

    def to_dict(self) -> dict[str, Any]:
        return {"code": self.code, "message": self.message, "context": self.context}


class InvalidStateTransition(DomainError):
    code = "invalid_state_transition"


class InvariantViolation(DomainError):
    code = "invariant_violation"


class ResourceNotFound(DomainError):
    code = "not_found"


class DuplicateResource(DomainError):
    code = "duplicate_resource"


class ConcurrencyConflict(DomainError):
    code = "concurrency_conflict"


class Unauthenticated(DomainError):
    """No usable credential was presented."""

    code = "unauthenticated"


class AuthorizationDenied(DomainError):
    code = "authorization_denied"


class ValidationFailed(DomainError):
    code = "validation_failed"


class ExternalServiceUnavailable(DomainError):
    code = "external_service_unavailable"


class RateLimited(DomainError):
    code = "rate_limited"


class ProcessingDeferred(DomainError):
    """Raised when a use case cannot complete synchronously and must be retried later."""

    code = "processing_deferred"