"""File one plainte against a provider, and re-assess his standing.

Both writes belong in the same transaction. If the complaint committed and the
standing did not, the provider would keep being put forward past the threshold
and nothing would ever retry it: the hide is computed when a complaint arrives
and at no other moment — there is no scheduler in this design and none is
wanted at 1 000 users/day.

The standing is recomputed from the complaints table rather than incremented,
so a retry or two concurrent complaints cannot inflate the tally and hide a
provider on complaints that were never filed.

What this use case does **not** do is sanction anybody. Past the threshold Kora
hides the provider and stops there (§6); the ban, if there is one, is decided by
a human in :mod:`decide_complaint`.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import datetime

import structlog

from loka.bounded_contexts.reviews_complaints.application.dto.reviews_dto import ComplaintView
from loka.bounded_contexts.reviews_complaints.application.ports import IntroductionDirectory
from loka.bounded_contexts.reviews_complaints.domain.entities.complaint import (
    Complaint,
    ComplaintReason,
)
from loka.bounded_contexts.reviews_complaints.domain.entities.provider_standing import (
    ProviderStanding,
    StandingStatus,
)
from loka.bounded_contexts.reviews_complaints.domain.exceptions import (
    AlreadySpoken,
    NoStandingToSpeak,
)
from loka.bounded_contexts.reviews_complaints.domain.repositories import (
    ComplaintRepository,
    ProviderStandingRepository,
)
from loka.bounded_contexts.reviews_complaints.domain.value_objects.provider import (
    IntroductionRef,
    OfferRef,
    ProviderRef,
    ServiceDomain,
    provider_kind_for,
)
from loka.shared.application.unit_of_work import UnitOfWork
from loka.shared.domain.errors import ValidationFailed
from loka.shared.domain.identifiers import new_id

COMPLAINT_REPOSITORY = "provider_complaint"
STANDING_REPOSITORY = "provider_standing"


@dataclass(frozen=True, slots=True)
class FileComplaintCommand:
    domain: ServiceDomain
    complainant_id: uuid.UUID
    provider_id: uuid.UUID
    offer_id: uuid.UUID
    introduction_id: uuid.UUID
    reason: ComplaintReason
    account: str


class FileComplaintUseCase:
    name = "file_complaint"

    def __init__(
        self,
        uow: UnitOfWork,
        *,
        introductions: IntroductionDirectory,
        hide_threshold: int,
        logger: structlog.stdlib.BoundLogger | None = None,
    ) -> None:
        self._uow = uow
        self._introductions = introductions
        self._hide_threshold = hide_threshold
        self._logger = logger or structlog.get_logger(self.name)

    async def execute(self, command: FileComplaintCommand, *, now: datetime) -> ComplaintView:
        complaints: ComplaintRepository = self._uow.repository(COMPLAINT_REPOSITORY)
        standings: ProviderStandingRepository = self._uow.repository(STANDING_REPOSITORY)

        provider = ProviderRef(
            kind=provider_kind_for(command.domain), provider_id=command.provider_id
        )
        offer = OfferRef(domain=command.domain, offer_id=command.offer_id)
        introduction = IntroductionRef(
            domain=command.domain, introduction_id=command.introduction_id
        )

        if not command.account.strip():
            # The only field a human will actually read before deciding. An
            # empty complaint is a vote, and votes do not sanction anyone.
            raise ValidationFailed("a complaint must say what happened")

        if not await self._introductions.introduced(
            introduction=introduction,
            user_id=command.complainant_id,
            provider=provider,
            offer=offer,
        ):
            raise NoStandingToSpeak(
                "Kora did not introduce this user to this provider",
                context={
                    "domain": command.domain.value,
                    "introduction_id": str(command.introduction_id),
                },
            )

        if await complaints.exists_for_introduction(introduction):
            raise AlreadySpoken(
                "this introduction has already produced a complaint",
                context={"introduction_id": str(command.introduction_id)},
            )

        complaint = Complaint(
            complaint_id=new_id(),
            provider=provider,
            offer=offer,
            introduction=introduction,
            complainant_id=command.complainant_id,
            reason=command.reason,
            account=command.account,
            now=now,
        )
        await complaints.add(complaint)

        standing = await standings.get(provider)
        is_new_standing = standing is None
        if standing is None:
            standing = ProviderStanding(standing_id=new_id(), provider=provider, now=now)

        # Counted after the insert, so this complaint is part of the count that
        # decides the hide. The repository reads the row just added through the
        # same session.
        load = await complaints.load_for_provider(provider)
        standing.reassess(
            pending_complaints=load.pending,
            upheld_complaints=load.upheld,
            hide_threshold=self._hide_threshold,
            now=now,
        )
        if is_new_standing:
            await standings.add(standing)
        else:
            await standings.save(standing)

        self._uow.collect(complaint)
        self._uow.collect(standing)
        await self._uow.commit()
        self._logger.info(
            "complaint_filed",
            complaint_id=str(complaint.id),
            domain=command.domain.value,
            provider_id=str(command.provider_id),
            reason=command.reason.value,
            pending_complaints=load.pending,
            hide_threshold=self._hide_threshold,
            standing=standing.status.value,
        )
        return _view(complaint, standing_status=standing.status)


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
