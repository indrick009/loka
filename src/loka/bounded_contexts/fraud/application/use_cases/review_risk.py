"""Manual risk-profile decisions (human-in-the-loop).

Risk profiles are recommendations; only an analyst can clear signals or lift
a temporary restriction, and every decision is recorded as a domain event.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import datetime
from enum import StrEnum

from loka.bounded_contexts.fraud.application.dto.fraud_dto import RiskProfileView
from loka.shared.application.unit_of_work import UnitOfWork
from loka.shared.application.use_case import UseCase
from loka.shared.domain.errors import ResourceNotFound, ValidationFailed

REPOSITORY_NAME = "fraud_profile"


class ReviewRiskDecision(StrEnum):
    CLEAR = "CLEAR"
    LIFT = "LIFT"


@dataclass(frozen=True, slots=True)
class ReviewRiskCommand:
    profile_id: uuid.UUID
    decision: ReviewRiskDecision
    actor_id: uuid.UUID
    note: str | None = None


class ReviewRiskUseCase(UseCase[ReviewRiskCommand, RiskProfileView]):
    name = "fraud.review_risk"

    def __init__(self, uow: UnitOfWork) -> None:
        super().__init__(uow)
        self._profiles = uow.repository(REPOSITORY_NAME)

    async def execute(  # type: ignore[override]
        self, command: ReviewRiskCommand, *, now: datetime) -> RiskProfileView:
        profile = await self._profiles.get(command.profile_id)
        if profile is None:
            raise ResourceNotFound(
                "risk profile not found", context={"profile_id": str(command.profile_id)}
            )

        if command.decision is ReviewRiskDecision.CLEAR:
            if not command.note or not command.note.strip():
                raise ValidationFailed("a note is required to clear a risk profile")
            profile.clear_signals(now=now, note=command.note, actor_id=command.actor_id)
        else:
            profile.lift_restriction(now=now, actor_id=command.actor_id)

        await self._profiles.save(profile)
        self._uow.collect(profile)
        await self._uow.commit()
        self._log(
            "risk_reviewed",
            profile_id=str(profile.id),
            decision=command.decision.value,
            actor_id=str(command.actor_id),
        )
        return RiskProfileView.from_profile(profile)
