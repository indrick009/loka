"""The human's decision on one plainte, and on the provider behind it.

This is the use case §6 exists for. It takes a reviewer id and will not run
without one, because every outcome it can produce is somebody's decision:
upholding a complaint, dismissing it, banning a provider, putting him back.

Three outcomes, kept separate on purpose:

* ``DISMISS`` — the complaint does not hold. The standing is recounted, which
  is what releases a provider the threshold had hidden. Without this path an
  honest provider taken down by bad-faith complaints would stay hidden for
  good.
* ``UPHOLD`` — the complaint holds. The provider is *not* banned by this: the
  complaint is a finding, the sanction is a separate judgement.
* ``UPHOLD_AND_BAN`` — the complaint holds and the same human bans the
  provider, with his name on it.

The ban is never implied by a count. Banning a landlord cuts off a revenue on
the strength of complaints Kora could not verify — she was not there — and a
competitor could otherwise take down an honest provider with a handful of them.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import datetime
from enum import StrEnum

import structlog

from loka.bounded_contexts.reviews_complaints.application.dto.reviews_dto import ComplaintView
from loka.bounded_contexts.reviews_complaints.domain.entities.complaint import Complaint
from loka.bounded_contexts.reviews_complaints.domain.entities.provider_standing import (
    ProviderStanding,
    StandingStatus,
)
from loka.bounded_contexts.reviews_complaints.domain.exceptions import SanctionRequiresHuman
from loka.bounded_contexts.reviews_complaints.domain.repositories import (
    ComplaintRepository,
    ProviderStandingRepository,
)
from loka.bounded_contexts.reviews_complaints.domain.value_objects.provider import (
    ProviderKind,
    ProviderRef,
)
from loka.shared.application.unit_of_work import UnitOfWork
from loka.shared.domain.errors import ResourceNotFound, ValidationFailed
from loka.shared.domain.identifiers import new_id

COMPLAINT_REPOSITORY = "provider_complaint"
STANDING_REPOSITORY = "provider_standing"


class ComplaintDecision(StrEnum):
    DISMISS = "DISMISS"
    UPHOLD = "UPHOLD"
    UPHOLD_AND_BAN = "UPHOLD_AND_BAN"


@dataclass(frozen=True, slots=True)
class DecideComplaintCommand:
    complaint_id: uuid.UUID
    reviewer_id: uuid.UUID
    decision: ComplaintDecision
    resolution: str


class DecideComplaintUseCase:
    name = "decide_complaint"

    def __init__(
        self,
        uow: UnitOfWork,
        *,
        hide_threshold: int,
        logger: structlog.stdlib.BoundLogger | None = None,
    ) -> None:
        self._uow = uow
        self._hide_threshold = hide_threshold
        self._logger = logger or structlog.get_logger(self.name)

    async def execute(self, command: DecideComplaintCommand, *, now: datetime) -> ComplaintView:
        complaints: ComplaintRepository = self._uow.repository(COMPLAINT_REPOSITORY)
        standings: ProviderStandingRepository = self._uow.repository(STANDING_REPOSITORY)

        if not command.resolution.strip():
            # The resolution is the record of why a provider was or was not
            # sanctioned. A decision with no reason cannot be defended later.
            raise ValidationFailed("a decision must say what was decided and why")

        reviewer_id = _require_reviewer(command.reviewer_id)
        complaint = await complaints.get(command.complaint_id)
        if complaint is None:
            raise ResourceNotFound(
                "no such complaint",
                context={"complaint_id": str(command.complaint_id)},
            )

        if command.decision is ComplaintDecision.DISMISS:
            complaint.dismiss(
                reviewer_id=reviewer_id, resolution=command.resolution, now=now
            )
        else:
            complaint.uphold(
                reviewer_id=reviewer_id, resolution=command.resolution, now=now
            )
        await complaints.save(complaint)

        standing = await standings.get(complaint.provider)
        is_new_standing = standing is None
        if standing is None:
            standing = ProviderStanding(
                standing_id=new_id(), provider=complaint.provider, now=now
            )

        # Recounted after the decision so a dismissal takes the complaint out of
        # the pending count in the same transaction that decided it.
        load = await complaints.load_for_provider(complaint.provider)
        standing.reassess(
            pending_complaints=load.pending,
            upheld_complaints=load.upheld,
            hide_threshold=self._hide_threshold,
            now=now,
        )
        ban_asked = command.decision is ComplaintDecision.UPHOLD_AND_BAN
        if ban_asked and standing.status is not StandingStatus.BANNED:
            standing.ban(decided_by=reviewer_id, note=command.resolution, now=now)
        if is_new_standing:
            await standings.add(standing)
        else:
            await standings.save(standing)

        self._uow.collect(complaint)
        self._uow.collect(standing)
        await self._uow.commit()
        self._logger.info(
            "complaint_decided",
            complaint_id=str(complaint.id),
            reviewer_id=str(reviewer_id),
            decision=command.decision.value,
            provider_id=str(complaint.provider.provider_id),
            standing=standing.status.value,
            pending_complaints=load.pending,
        )
        return _view(complaint, standing_status=standing.status)


@dataclass(frozen=True, slots=True)
class ReinstateProviderCommand:
    provider_kind: ProviderKind
    provider_id: uuid.UUID
    reviewer_id: uuid.UUID
    note: str


class ReinstateProviderUseCase:
    """Put a hidden or banned provider back in front of users.

    Separate from a complaint decision because it is about the provider, not
    about one grievance: a provider hidden by three complaints is cleared once,
    not three times.

    It exists because the automatic hide acts on complaints Kora could not
    verify. Without a way back, the honest landlord caught by a competitor's
    complaints would wait for them to be decided one by one while his listings
    stayed invisible.
    """

    name = "reinstate_provider"

    def __init__(
        self,
        uow: UnitOfWork,
        *,
        logger: structlog.stdlib.BoundLogger | None = None,
    ) -> None:
        self._uow = uow
        self._logger = logger or structlog.get_logger(self.name)

    async def execute(self, command: ReinstateProviderCommand, *, now: datetime) -> None:
        standings: ProviderStandingRepository = self._uow.repository(STANDING_REPOSITORY)
        reviewer_id = _require_reviewer(command.reviewer_id)

        provider = ProviderRef(kind=command.provider_kind, provider_id=command.provider_id)
        standing = await standings.get(provider)
        if standing is None:
            raise ResourceNotFound(
                "this provider has no standing to clear",
                context={"provider_id": str(command.provider_id)},
            )

        standing.reinstate(decided_by=reviewer_id, note=command.note, now=now)
        await standings.save(standing)
        self._uow.collect(standing)
        await self._uow.commit()
        self._logger.info(
            "provider_reinstated",
            provider_id=str(command.provider_id),
            reviewer_id=str(reviewer_id),
        )


def _require_reviewer(reviewer_id: uuid.UUID) -> uuid.UUID:
    """Refuse the nil UUID on the sanction path.

    Kora may hide a provider, never ban or clear one (§6). A transport that
    reaches here without an authenticated operator carries ``UUID(int=0)``
    rather than nothing, and that would be stored as the human who decided —
    a decision with nobody to ask about it.
    """
    if reviewer_id.int == 0:
        raise SanctionRequiresHuman("a sanction must be decided by a named reviewer")
    return reviewer_id


def _view(complaint: Complaint, *, standing_status: StandingStatus) -> ComplaintView:
    return ComplaintView(
        complaint_id=str(complaint.id),
        domain=complaint.offer.domain.value,
        provider_id=str(complaint.provider.provider_id),
        reason=complaint.reason.value,
        account=complaint.account,
        status=complaint.status.value,
        filed_at=complaint.filed_at,
        decided_at=complaint.decided_at,
        resolution=complaint.resolution,
        provider_hidden=standing_status is not StandingStatus.VISIBLE,
    )
