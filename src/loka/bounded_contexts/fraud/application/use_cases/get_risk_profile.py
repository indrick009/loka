"""Risk profile lookup for the analyst console."""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import datetime

from loka.bounded_contexts.fraud.application.dto.fraud_dto import RiskProfileView
from loka.bounded_contexts.fraud.domain.entities.risk_profile import RiskSubject
from loka.shared.application.unit_of_work import UnitOfWork
from loka.shared.application.use_case import UseCase
from loka.shared.domain.errors import ResourceNotFound

REPOSITORY_NAME = "fraud_profile"


@dataclass(frozen=True, slots=True)
class RiskProfileQuery:
    subject: RiskSubject
    subject_id: uuid.UUID


class GetRiskProfileUseCase(UseCase[RiskProfileQuery, RiskProfileView]):
    name = "fraud.get_risk_profile"

    def __init__(self, uow: UnitOfWork) -> None:
        super().__init__(uow)
        self._profiles = uow.repository(REPOSITORY_NAME)

    async def execute(  # type: ignore[override]
        self, command: RiskProfileQuery, *, now: datetime
    ) -> RiskProfileView:
        profile = await self._profiles.get_by_subject(
            command.subject.value, command.subject_id
        )
        if profile is None:
            raise ResourceNotFound(
                "no risk profile for this subject",
                context={
                    "subject": command.subject.value,
                    "subject_id": str(command.subject_id),
                },
            )
        return RiskProfileView.from_profile(profile)