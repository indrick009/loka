"""Risk evaluation: facts in, signals scored, profile updated.

Recomputing instead of incrementing keeps the score explainable and makes it
safe to re-run after evidence changes (a dismissed report quietens the score
on the next evaluation).
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import datetime

from loka.bounded_contexts.fraud.application.dto.fraud_dto import RiskProfileView
from loka.bounded_contexts.fraud.application.risk_evaluator import RiskEvaluator
from loka.bounded_contexts.fraud.domain.entities.risk_profile import RiskSubject
from loka.shared.application.unit_of_work import UnitOfWork
from loka.shared.application.use_case import UseCase


@dataclass(frozen=True, slots=True)
class EvaluateRiskCommand:
    subject: RiskSubject
    subject_id: uuid.UUID


class EvaluateRiskUseCase(UseCase[EvaluateRiskCommand, RiskProfileView]):
    name = "fraud.evaluate_risk"

    def __init__(self, uow: UnitOfWork) -> None:
        super().__init__(uow)
        self._evaluator = RiskEvaluator(uow)

    async def execute(  # type: ignore[override]
        self, command: EvaluateRiskCommand, *, now: datetime
    ) -> RiskProfileView:
        profile = await self._evaluator.apply(command.subject, command.subject_id, now)
        self._uow.collect(profile)
        await self._uow.commit()
        self._log(
            "risk_evaluated",
            subject=command.subject.value,
            subject_id=str(command.subject_id),
            score=profile.current_score.score,
            band=profile.current_score.band.value,
        )
        return RiskProfileView.from_profile(profile)