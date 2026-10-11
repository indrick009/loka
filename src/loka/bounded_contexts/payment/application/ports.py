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
    # How long the provider will keep accepting money on this handle. The
    # platform's own 30-minute default is a guess; a provider that states its
    # window replaces the guess, so a payer is never told a link died while
    # the provider would still have honoured it.
    link_ttl_seconds: int | None = None
    raw: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True, slots=True)
class VerifiedCallback:
    """A provider callback after signature verification.

    ``accepted`` being False means the message could not be acted on at all —
    an invalid signature, or a status the adapter does not recognise.
    ``status`` is raw; the domain maps it to a transition, and a reported
    failure is an accepted verdict, not a rejected one.

    ``audit_digest`` is what may be written to the callback log. Some providers
    authenticate with a shared secret echoed in a header rather than a
    signature over the body; storing that header would copy a live credential
    into the database, so the adapter supplies a digest of the body instead.

    ``authoritative`` marks a verdict the platform fetched from the provider
    rather than one the provider pushed. Only a fetched verdict may settle a
    payment confirmed long ago: a pushed one has to be fresh, because an old
    pushed message is indistinguishable from a replayed one.
    """

    provider_event_id: str
    provider_reference: str
    accepted: bool
    status: str
    rejection_reason: str | None = None
    provider_reported_at: datetime | None = None
    payment_id: str | None = None
    audit_digest: str | None = None
    authoritative: bool = False

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


@runtime_checkable
class PaymentStatusSource(Protocol):
    """Asks the provider what a transaction's status actually is.

    Separate from :class:`PaymentGateway` because not every provider needs it:
    one that signs its webhooks over the body has already proved what it says,
    and the port stays small for it. A provider that only identifies itself has
    proved who is calling and nothing more, so its claim has to be checked.

    Two consumers, which is why this is a port and not a method on an adapter:
    the callback handler confirms what it was told, and reconciliation asks
    about payments it was never told about at all.
    """

    async def fetch_status(self, *, provider_reference: str) -> VerifiedCallback: ...