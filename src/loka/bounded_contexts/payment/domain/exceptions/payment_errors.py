"""Payment context errors."""

from __future__ import annotations

from loka.shared.domain.errors import DomainError


class PaymentGatewayError(DomainError):
    """The payment provider could not be reached or refused the request.

    This is a provider failure, not a business rule: the caller may retry, and
    the use case must never treat it as a settled outcome.
    """

    code = "payment_gateway_error"


class CallbackRejected(DomainError):
    """A provider callback failed verification (bad signature or invalid data).

    The callback is recorded for reconciliation but no state changes.
    """

    code = "callback_rejected"


class PaymentAlreadySettled(DomainError):
    """A tenant attempted to pay an application that already has an active grant."""

    code = "payment_already_settled"


class AvailabilityNotConfirmed(DomainError):
    """The landlord has not said the lodging is still free, so nobody pays.

    A business rule, not a failure: the tenant is told the landlord is being
    asked, and offered other lodgings meanwhile. Charging 1 000 FCFA for a
    contact that leads to an already-rented room is the single outcome the
    availability check exists to prevent (housing spec §9).
    """

    code = "availability_not_confirmed"
