"""Escalation queue the analyst works from."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime

from loka.bounded_contexts.fraud.application.dto.fraud_dto import (
    EscalationQueueView,
    RiskProfileView,
)
from loka.shared.application.unit_of_work import UnitOfWork
from loka.shared.application.use_case import UseCase

REPOSITORY_NAME = "fraud_profile"


@dataclass(frozen=True, slots=True)
class EscalationQueueQuery:
    limit: int = 50


class GetEscalationQueueUseCase(UseCase[EscalationQueueQuery, EscalationQueueView]):
    name = "fraud.get_escalation_queue"

    def __init__(self, uow: UnitOfWork) -> None:
        super().__init__(uow)
        self._profiles = uow.repository(REPOSITORY_NAME)

    async def execute(  # type: ignore[override]
        self, command: EscalationQueueQuery, *, now: datetime
    ) -> EscalationQueueView:
        profiles = await self._profiles.list_escalations(limit=command.limit)
        items = [RiskProfileView.from_profile(profile) for profile in profiles]
        return EscalationQueueView(items=items, total=len(items))