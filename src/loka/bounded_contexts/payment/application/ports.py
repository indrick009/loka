"""Payment gateway port.

The README contract: "le domaine ne doit pas être couplé directement au
fournisseur de paiement." A :class:`PaymentGateway` is a provider adapter: it
creates the provider-side reference and verifies callbacks. The domain only
ever sees the resulting, already-verified data.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Protocol, runtime_checkable

from loka.shared.domain.clock import ensure_utc


@dataclass(frozen=True, slots=True)
class PaymentInitiation:
    """The provider-side handle for a payment, created before any confirmation."""

    provider: str
    provider_reference: str
    provider_url: str | None = None
    raw: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True, slots=True)
class VerifiedCallback:
    """A provider callback after signature verification.

    ``accepted`` being False means the signature was valid but the provider
    reported a failure; ``rejected`` means the signature itself was invalid.
    ``status`` is raw; the domain maps it to a transition.
    """

    provider_event_id: str
    provider_reference: str
    accepted: bool
    status: str
    rejection_reason: str | None = None
    provider_reported_at: datetime | None = None
    payment_id: str | None = None

    @property
    def reported_at(self) -> datetime:
        return ensure_utc(self.provider_reported_at or datetime.now())


@runtime_checkable
class PaymentGateway(Protocol):
    """Contract every payment provider adapter must satisfy.

    ``exports`` and ``presigned_url`` are intentionally absent: providers that
    need them expose them on their own concrete type, keeping this port small.
    """

    def name(self) -> str: ...

    async def initiate(
        self, *, payment_id: str, amount_xaf: int, **kwargs: Any
    ) -> PaymentInitiation: ...

    def verify_callback(
        self, *, raw_body: bytes, signature: str, headers: dict[str, str]
    ) -> VerifiedCallback: ...