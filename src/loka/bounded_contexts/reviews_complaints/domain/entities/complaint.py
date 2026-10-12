"""The plainte: what someone discovered after Kora had already left.

A complaint is **punitive** — it exists so the team knows which provider to
sanction or ban (§2) — and it is the only record in this context that carries
free text, because a human reads it before deciding (§5).

Every real case the product owner gave is the same shape: « le bailleur avait
dit avec eau, il n'y a pas l'eau », « elle a menti, photo générée par IA »,
« on commande, elle ne vient pas ». The person found out *afterwards*. Kora
cannot verify any of it — she was not there — which is exactly why a complaint
is the only way she learns, and exactly why she must not act on it alone.

So the lifecycle ends on a human: ``uphold`` and ``dismiss`` both demand a
reviewer id. Nothing in this module bans anybody; it records what was said and
who decided what about it.
"""

from __future__ import annotations

import uuid
from datetime import datetime
from enum import StrEnum

from loka.bounded_contexts.reviews_complaints.domain.value_objects.provider import (
    IntroductionRef,
    OfferRef,
    ProviderRef,
)
from loka.shared.domain.aggregate import AggregateRoot
from loka.shared.domain.clock import ensure_utc
from loka.shared.domain.errors import InvalidStateTransition

MAX_ACCOUNT_LENGTH = 2000
MAX_RESOLUTION_LENGTH = 1000


class ComplaintReason(StrEnum):
    """The grievances §4 and §7 actually name, and nothing speculative."""

    PROVIDER_UNREACHABLE = "PROVIDER_UNREACHABLE"
    OFFER_ALREADY_GONE = "OFFER_ALREADY_GONE"
    OFFER_NOT_AS_DESCRIBED = "OFFER_NOT_AS_DESCRIBED"
    MISLEADING_PHOTOS = "MISLEADING_PHOTOS"
    NO_SHOW = "NO_SHOW"
    ASKED_FOR_MONEY = "ASKED_FOR_MONEY"
    INAPPROPRIATE_BEHAVIOUR = "INAPPROPRIATE_BEHAVIOUR"
    OTHER = "OTHER"


class ComplaintStatus(StrEnum):
    """Waiting on the queue, with someone, or decided by that someone."""

    OPEN = "OPEN"
    UNDER_REVIEW = "UNDER_REVIEW"
    DISMISSED = "DISMISSED"
    UPHELD = "UPHELD"


DECIDED_STATUSES = frozenset({ComplaintStatus.DISMISSED, ComplaintStatus.UPHELD})


class Complaint(AggregateRoot):
    __slots__ = (
        "account",
        "complainant_id",
        "complaint_id",
        "decided_at",
        "filed_at",
        "introduction",
        "offer",
        "provider",
        "reason",
        "resolution",
        "reviewer_id",
        "status",
        "updated_at",
    )

    def __init__(
        self,
        *,
        complaint_id: uuid.UUID,
        provider: ProviderRef,
        offer: OfferRef,
        introduction: IntroductionRef,
        complainant_id: uuid.UUID,
        reason: ComplaintReason,
        account: str,
        now: datetime,
    ) -> None:
        super().__init__(aggregate_type="Complaint")
        self._assign_id(complaint_id)
        self.complaint_id = complaint_id
        self.provider = provider
        self.offer = offer
        self.introduction = introduction
        self.complainant_id = complainant_id
        self.reason = reason
        # Trimmed, not rejected on length: the complainant is already unhappy
        # and a validation error at this point loses the only account Kora will
        # ever get of what happened.
        self.account = account.strip()[:MAX_ACCOUNT_LENGTH]
        self.status = ComplaintStatus.OPEN
        self.reviewer_id: uuid.UUID | None = None
        self.resolution: str | None = None
        self.filed_at = ensure_utc(now)
        self.decided_at: datetime | None = None
        self.updated_at = ensure_utc(now)
        self.record(
            "ComplaintFiled",
            occurred_at=now,
            actor_id=complainant_id,
            payload={
                "complaint_id": str(complaint_id),
                "domain": offer.domain.value,
                "provider_kind": provider.kind.value,
                "provider_id": str(provider.provider_id),
                "reason": reason.value,
            },
        )

    @property
    def is_decided(self) -> bool:
        return self.status in DECIDED_STATUSES

    @property
    def is_pending(self) -> bool:
        """Still owed an answer, which is what the queue's delay is measured on."""
        return not self.is_decided

    def take_for_review(self, *, reviewer_id: uuid.UUID, now: datetime) -> None:
        """A named human picks it up off the queue.

        Re-assignment is refused once decided: reopening a decision by
        overwriting its reviewer would erase who answered for the sanction.
        """
        if self.is_decided:
            raise InvalidStateTransition(
                f"a {self.status.value} complaint cannot be taken for review",
                context={"complaint_id": str(self.id)},
            )
        self.status = ComplaintStatus.UNDER_REVIEW
        self.reviewer_id = reviewer_id
        self._touch(now)
        self.record(
            "ComplaintUnderReview",
            occurred_at=now,
            actor_id=reviewer_id,
            payload={"complaint_id": str(self.id)},
        )

    def uphold(self, *, reviewer_id: uuid.UUID, resolution: str, now: datetime) -> None:
        """The team believes the complainant. Still not a ban by itself.

        Upholding is a finding about this one complaint; what happens to the
        provider is decided on his standing, by the same human.
        """
        self._assert_undecided(action="upholding")
        self.status = ComplaintStatus.UPHELD
        self._decide(reviewer_id=reviewer_id, resolution=resolution, now=now)
        self.record(
            "ComplaintUpheld",
            occurred_at=now,
            actor_id=reviewer_id,
            payload={
                "complaint_id": str(self.id),
                "provider_id": str(self.provider.provider_id),
                "resolution": self.resolution,
            },
        )

    def dismiss(self, *, reviewer_id: uuid.UUID, resolution: str, now: datetime) -> None:
        """The team does not believe it, or cannot tell.

        Dismissing matters as much as upholding: it is what releases a provider
        hidden by the threshold, and a queue with no way to say "no" would hide
        honest providers for good.
        """
        self._assert_undecided(action="dismissing")
        self.status = ComplaintStatus.DISMISSED
        self._decide(reviewer_id=reviewer_id, resolution=resolution, now=now)
        self.record(
            "ComplaintDismissed",
            occurred_at=now,
            actor_id=reviewer_id,
            payload={
                "complaint_id": str(self.id),
                "provider_id": str(self.provider.provider_id),
                "resolution": self.resolution,
            },
        )

    def _assert_undecided(self, *, action: str) -> None:
        if self.is_decided:
            raise InvalidStateTransition(
                f"{action} requires an undecided complaint, this one is {self.status.value}",
                context={"complaint_id": str(self.id), "status": self.status.value},
            )

    def _decide(self, *, reviewer_id: uuid.UUID, resolution: str, now: datetime) -> None:
        self.reviewer_id = reviewer_id
        self.resolution = resolution.strip()[:MAX_RESOLUTION_LENGTH]
        self.decided_at = ensure_utc(now)
        self._touch(now)

    def _touch(self, now: datetime) -> None:
        self.updated_at = ensure_utc(now)
        self._bump()
