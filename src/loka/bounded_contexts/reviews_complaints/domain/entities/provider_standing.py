"""Whether Kora still puts a provider forward, and who decided that.

This is the one aggregate that can cost a provider his income, so §6 is written
into its transitions rather than into the use case that calls them.

**Kora never bans on her own.** ``ban`` and ``reinstate`` take a reviewer id
because a ban is a human's decision with a human's name on it. Banning a
landlord on unverified complaints would cut off his revenue on the word of
people Kora cannot cross-examine; a competitor or a tenant acting in bad faith
would take down an honest landlord with a handful of complaints and nobody
would catch it.

**Hiding is the automatic part, and it is not banning.** Past the configured
threshold the provider stops being put forward until someone decides. It is
reversible without damage, which is the whole reason it is allowed to happen
without a human: the worst case is a few days of lost visibility, not a lost
livelihood.

``HIDDEN`` is therefore reachable only through :meth:`reassess`, and ``BANNED``
only through :meth:`ban`. Keeping the two doors separate is what stops a future
caller from quietly wiring the threshold into a ban.
"""

from __future__ import annotations

import uuid
from datetime import datetime
from enum import StrEnum

from loka.bounded_contexts.reviews_complaints.domain.value_objects.provider import ProviderRef
from loka.shared.domain.aggregate import AggregateRoot
from loka.shared.domain.clock import ensure_utc
from loka.shared.domain.errors import InvalidStateTransition, InvariantViolation

MAX_DECISION_LENGTH = 1000


class StandingStatus(StrEnum):
    VISIBLE = "VISIBLE"
    HIDDEN = "HIDDEN"
    BANNED = "BANNED"


class ProviderStanding(AggregateRoot):
    __slots__ = (
        "created_at",
        "decided_at",
        "decided_by",
        "decision_note",
        "hidden_at",
        "pending_complaints",
        "provider",
        "standing_id",
        "status",
        "updated_at",
        "upheld_complaints",
    )

    def __init__(
        self,
        *,
        standing_id: uuid.UUID,
        provider: ProviderRef,
        now: datetime,
    ) -> None:
        super().__init__(aggregate_type="ProviderStanding")
        self._assign_id(standing_id)
        self.standing_id = standing_id
        self.provider = provider
        self.status = StandingStatus.VISIBLE
        self.pending_complaints = 0
        self.upheld_complaints = 0
        self.hidden_at: datetime | None = None
        self.decided_by: uuid.UUID | None = None
        self.decided_at: datetime | None = None
        self.decision_note: str | None = None
        self.created_at = ensure_utc(now)
        self.updated_at = ensure_utc(now)

    @property
    def is_put_forward(self) -> bool:
        """Whether the ranking may still offer this provider at all."""
        return self.status is StandingStatus.VISIBLE

    def reassess(
        self,
        *,
        pending_complaints: int,
        upheld_complaints: int,
        hide_threshold: int,
        now: datetime,
    ) -> None:
        """Recount the complaints against this provider and hide or release him.

        The counts are passed in rather than incremented so the tally can never
        drift from the complaints table: an increment lost to a rollback, or
        applied twice by two workers, would hide a provider nobody complained
        about that often.

        ``pending_complaints`` counts complaints still owed an answer, each of
        which is capped at one per introduction. Without that cap one furious
        tenant could file the whole threshold by himself.

        A banned provider is left alone: only a human undoes a ban.
        """
        if hide_threshold < 1:
            raise InvariantViolation(
                "the hide threshold must be at least one complaint",
                context={"hide_threshold": hide_threshold},
            )
        self.pending_complaints = max(pending_complaints, 0)
        self.upheld_complaints = max(upheld_complaints, 0)
        if self.status is StandingStatus.BANNED:
            self._touch(now)
            return

        reached = self.pending_complaints >= hide_threshold
        if reached and self.status is StandingStatus.VISIBLE:
            self._hide(hide_threshold=hide_threshold, now=now)
        elif not reached and self.status is StandingStatus.HIDDEN:
            self._unhide(now=now)
        else:
            self._touch(now)

    def ban(self, *, decided_by: uuid.UUID, note: str, now: datetime) -> None:
        """A human bans the provider. There is no other way to reach this state."""
        if self.status is StandingStatus.BANNED:
            raise InvalidStateTransition(
                "this provider is already banned",
                context={"provider_id": str(self.provider.provider_id)},
            )
        self.status = StandingStatus.BANNED
        self._record_decision(decided_by=decided_by, note=note, now=now)
        self.record(
            "ProviderBanned",
            occurred_at=now,
            actor_id=decided_by,
            payload={
                "provider_kind": self.provider.kind.value,
                "provider_id": str(self.provider.provider_id),
                "upheld_complaints": self.upheld_complaints,
                "note": self.decision_note,
            },
        )

    def reinstate(self, *, decided_by: uuid.UUID, note: str, now: datetime) -> None:
        """A human clears the provider, from a hide or from a ban.

        Needed as much as the ban: the threshold hides on complaints Kora could
        not verify, so the honest provider caught by three bad-faith complaints
        has to have a way back that does not wait for them to expire.
        """
        if self.status is StandingStatus.VISIBLE:
            raise InvalidStateTransition(
                "this provider is already put forward",
                context={"provider_id": str(self.provider.provider_id)},
            )
        previous = self.status
        self.status = StandingStatus.VISIBLE
        self.hidden_at = None
        self._record_decision(decided_by=decided_by, note=note, now=now)
        self.record(
            "ProviderReinstated",
            occurred_at=now,
            actor_id=decided_by,
            payload={
                "provider_kind": self.provider.kind.value,
                "provider_id": str(self.provider.provider_id),
                "previous_status": previous.value,
                "note": self.decision_note,
            },
        )

    def _hide(self, *, hide_threshold: int, now: datetime) -> None:
        self.status = StandingStatus.HIDDEN
        self.hidden_at = ensure_utc(now)
        self._touch(now)
        self.record(
            "ProviderHiddenPendingReview",
            occurred_at=now,
            payload={
                "provider_kind": self.provider.kind.value,
                "provider_id": str(self.provider.provider_id),
                "pending_complaints": self.pending_complaints,
                "hide_threshold": hide_threshold,
            },
        )

    def _unhide(self, *, now: datetime) -> None:
        self.status = StandingStatus.VISIBLE
        self.hidden_at = None
        self._touch(now)
        self.record(
            "ProviderPutForwardAgain",
            occurred_at=now,
            payload={
                "provider_kind": self.provider.kind.value,
                "provider_id": str(self.provider.provider_id),
                "pending_complaints": self.pending_complaints,
            },
        )

    def _record_decision(self, *, decided_by: uuid.UUID, note: str, now: datetime) -> None:
        self.decided_by = decided_by
        self.decided_at = ensure_utc(now)
        self.decision_note = note.strip()[:MAX_DECISION_LENGTH] or None
        self._touch(now)

    def _touch(self, now: datetime) -> None:
        self.updated_at = ensure_utc(now)
        self._bump()
