"""Ports exposed by the Landlord context, and the ones it depends on.

``LandlordDirectory`` is the outbound contract other contexts consume;
``VerificationAdjudicator`` and ``EvidenceUrlProvider`` are the inbound
contracts this context consumes. The application layer depends on these
protocols, never on OpenRouter or S3.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from typing import Protocol, runtime_checkable

from loka.bounded_contexts.landlord.domain.entities.verification_request import (
    DocumentKind,
)
from loka.bounded_contexts.landlord.domain.services.verification_adjudication import (
    Adjudication,
)


@runtime_checkable
class LandlordDirectory(Protocol):
    """Read-only view of a landlord's verification state.

    Consuming contexts (Property, Rental) depend on this port instead of the
    verification tables, so verification can be proven in more than one way
    without touching them.
    """

    async def is_verified(self, landlord_id: uuid.UUID) -> bool: ...

    async def profile_id_for_user(self, user_id: uuid.UUID) -> uuid.UUID | None:
        """Landlord profile owning this user, if any.

        Callers authenticate with a *user* id while listings carry a *profile*
        id, so ownership can only be decided through this lookup.
        """


@dataclass(frozen=True, slots=True)
class VerificationEvidence:
    """Everything the adjudicator is allowed to see.

    Only short-lived download URLs cross this boundary: the model provider
    fetches the encrypted evidence itself, and no document bytes or object keys
    ever enter a prompt.
    """

    request_id: uuid.UUID
    landlord_id: uuid.UUID
    document_kind: DocumentKind
    document_url: str
    selfie_url: str
    risk_score: int | None


@runtime_checkable
class VerificationAdjudicator(Protocol):
    """An automated first opinion on a verification request."""

    async def adjudicate(self, evidence: VerificationEvidence) -> Adjudication: ...


@runtime_checkable
class EvidenceUrlProvider(Protocol):
    """Mint a short-lived, pre-signed URL for a stored object."""

    async def url_for(self, object_key: str) -> str: ...
