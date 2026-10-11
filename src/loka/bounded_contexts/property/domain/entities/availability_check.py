"""One question put to one landlord: "le logement est encore disponible ?".

The business rule it exists to enforce is short (§9): **the tenant pays only if
the landlord confirms.** Everything else follows from that.

It is one question asked at the moment it counts, not a state tracked over
time. The spec is explicit — no periodic re-verification, no automatic
expiry, no decrement on a match or a payment — because the only thing that can
change a listing's availability is the landlord saying so. A check therefore
has exactly two ends: he answered, or he has not. "Pas de réponse" is not a
status, it is the absence of one, and it never confirms.

One open check per listing rather than per (landlord, tenant) pair: the message
asks about the lodging, not about a particular tenant, and asking twice because
two tenants are interested is noise the landlord did not sign up for. The
tenant who triggered it is recorded so the answer can be carried back to
someone.
"""

from __future__ import annotations

import uuid
from datetime import datetime
from enum import StrEnum

from loka.bounded_contexts.property.domain.value_objects.enums import AvailabilityAnswer
from loka.shared.domain.aggregate import AggregateRoot
from loka.shared.domain.clock import ensure_utc
from loka.shared.domain.errors import InvalidStateTransition


class CheckStatus(StrEnum):
    """Asked and waiting, or answered. Nothing in between and nothing after."""

    PENDING = "PENDING"
    ANSWERED = "ANSWERED"


# The answers that let a tenant pay. Only one of them, which is the point: a
# listing is confirmed available or it is not, and "one was taken" is a
# confirmation that *some* remain, so it also releases the payment.
CONFIRMING_ANSWERS = frozenset(
    {AvailabilityAnswer.STILL_AVAILABLE, AvailabilityAnswer.ONE_TAKEN}
)


class AvailabilityCheck(AggregateRoot):
    __slots__ = (
        "answer",
        "answered_at",
        "asked_at",
        "check_id",
        "landlord_id",
        "property_id",
        "status",
        "tenant_id",
    )

    def __init__(
        self,
        *,
        check_id: uuid.UUID,
        property_id: uuid.UUID,
        landlord_id: uuid.UUID,
        tenant_id: uuid.UUID | None,
        now: datetime,
    ) -> None:
        super().__init__(aggregate_type="AvailabilityCheck")
        self._assign_id(check_id)
        self.check_id = check_id
        self.property_id = property_id
        self.landlord_id = landlord_id
        self.tenant_id = tenant_id
        self.status = CheckStatus.PENDING
        self.asked_at = ensure_utc(now)
        self.answered_at: datetime | None = None
        self.answer: AvailabilityAnswer | None = None
        self.record(
            "AvailabilityCheckRequested",
            occurred_at=now,
            payload={
                "check_id": str(check_id),
                "property_id": str(property_id),
                "landlord_id": str(landlord_id),
            },
        )

    def record_answer(self, answer: AvailabilityAnswer, *, now: datetime) -> None:
        """What the landlord said, once.

        A second answer is refused rather than applied: the first one already
        moved the listing — decremented it, or took it out of search — and
        replaying that would cost a lodging the landlord never let go.
        """
        if self.status is CheckStatus.ANSWERED:
            raise InvalidStateTransition(
                "this availability check has already been answered",
                context={"check_id": str(self.id), "answer": str(self.answer)},
            )
        self.status = CheckStatus.ANSWERED
        self.answer = answer
        self.answered_at = ensure_utc(now)
        self.record(
            "AvailabilityCheckAnswered",
            occurred_at=now,
            payload={
                "check_id": str(self.id),
                "property_id": str(self.property_id),
                "answer": answer.value,
                "confirms_availability": self.confirms_availability,
            },
        )

    @property
    def confirms_availability(self) -> bool:
        """Whether this check releases the payment. Pending never does."""
        return self.answer in CONFIRMING_ANSWERS
