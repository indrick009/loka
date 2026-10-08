"""Decide a submitted verification.

Approving or rejecting a request also moves the profile it entitles, in the
same transaction: publication gates on the profile, so the two can never
disagree. The reviewer may be absent for an automatic decision, which is why
``reviewer_id`` is optional.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import datetime
from enum import StrEnum

from loka.bounded_contexts.landlord.application.dto.landlord_verification_dto import (
    LandlordVerificationView,
)
from loka.bounded_contexts.landlord.domain.entities.verification_request import (
    RejectionReason,
)
from loka.bounded_contexts.landlord.domain.repositories.verification_repository import (
    LANDLORD_PROFILE_REPOSITORY,
    VERIFICATION_REQUEST_REPOSITORY,
)
from loka.shared.application.unit_of_work import UnitOfWork
from loka.shared.application.use_case import UseCase
from loka.shared.domain.errors import ResourceNotFound


class ReviewDecision(StrEnum):
    APPROVE = "APPROVE"
    REJECT = "REJECT"


@dataclass(frozen=True, slots=True)
class ReviewLandlordVerificationCommand:
    request_id: uuid.UUID
    decision: ReviewDecision
    reviewer_id: uuid.UUID | None = None
    reason: RejectionReason | None = None
    notes: str | None = None


class ReviewLandlordVerificationUseCase(
    UseCase[ReviewLandlordVerificationCommand, LandlordVerificationView]
):
    name = "landlord.review_verification"

    def __init__(self, uow: UnitOfWork) -> None:
        super().__init__(uow)
        self._profiles = uow.repository(LANDLORD_PROFILE_REPOSITORY)
        self._requests = uow.repository(VERIFICATION_REQUEST_REPOSITORY)

    async def execute(  # type: ignore[override]
        self, command: ReviewLandlordVerificationCommand, *, now: datetime
    ) -> LandlordVerificationView:
        request = await self._requests.get(command.request_id)
        if request is None:
            raise ResourceNotFound(
                "verification request not found",
                context={"request_id": str(command.request_id)},
            )
        profile = await self._profiles.get(request.landlord_id)
        if profile is None:
            raise ResourceNotFound(
                "landlord profile not found for this request",
                context={"landlord_id": str(request.landlord_id)},
            )

        if command.decision is ReviewDecision.APPROVE:
            request.approve(now=now, reviewer_id=command.reviewer_id, notes=command.notes)
            profile.mark_verified(now=now)
        else:
            request.reject(
                reason=command.reason or RejectionReason.INSUFFICIENT_EVIDENCE,
                now=now,
                reviewer_id=command.reviewer_id,
                notes=command.notes,
            )
            profile.mark_rejected(now=now)

        await self._requests.save(request)
        await self._profiles.save(profile)
        self._uow.collect(request)
        self._uow.collect(profile)
        await self._uow.commit()
        self._log(
            "landlord_verification_reviewed",
            request_id=str(request.request_id),
            decision=command.decision.value,
            status=request.status.value,
        )
        return LandlordVerificationView.from_aggregates(request, profile)