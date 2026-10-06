"""Report submission.

A report is the human signal that feeds the machine signals: submitting one
also re-evaluates the target's risk profile in the same transaction, so the
escalation queue reflects the new evidence immediately.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import datetime

from loka.bounded_contexts.fraud.application.dto.fraud_dto import ReportView
from loka.bounded_contexts.fraud.application.risk_evaluator import RiskEvaluator
from loka.bounded_contexts.fraud.domain.entities.risk_profile import (
    Report,
    ReportReason,
    RiskSubject,
)
from loka.shared.application.unit_of_work import UnitOfWork
from loka.shared.application.use_case import UseCase
from loka.shared.domain.errors import ValidationFailed
from loka.shared.domain.identifiers import new_id

REPORT_REPOSITORY_NAME = "fraud_report"

_TARGET_SUBJECTS: dict[str, RiskSubject] = {
    "PROPERTY": RiskSubject.PROPERTY,
    "LANDLORD": RiskSubject.LANDLORD,
    "USER": RiskSubject.USER,
}


@dataclass(frozen=True, slots=True)
class SubmitReportCommand:
    reporter_id: uuid.UUID
    target_type: str
    target_id: uuid.UUID
    reason: ReportReason
    description: str
    evidence: list[str] | None = None


class SubmitReportUseCase(UseCase[SubmitReportCommand, ReportView]):
    name = "fraud.submit_report"

    def __init__(self, uow: UnitOfWork) -> None:
        super().__init__(uow)
        self._reports = uow.repository(REPORT_REPOSITORY_NAME)
        self._evaluator = RiskEvaluator(uow)

    async def execute(  # type: ignore[override]
        self, command: SubmitReportCommand, *, now: datetime) -> ReportView:
        subject = _TARGET_SUBJECTS.get(command.target_type.upper())
        if subject is None:
            raise ValidationFailed(
                "report target must be one of PROPERTY, LANDLORD, USER",
                context={"target_type": command.target_type},
            )
        if not command.description.strip():
            raise ValidationFailed("report description is required")

        report = Report(
            report_id=new_id(),
            reporter_id=command.reporter_id,
            reason=command.reason,
            description=command.description,
            target_type=command.target_type.upper(),
            target_id=command.target_id,
            now=now,
            evidence=command.evidence,
        )
        await self._reports.add(report)

        profile = await self._evaluator.apply(subject, command.target_id, now)
        self._uow.collect(report)
        self._uow.collect(profile)
        await self._uow.commit()
        self._log(
            "report_submitted",
            report_id=str(report.id),
            target_type=command.target_type,
            target_id=str(command.target_id),
            reason=command.reason.value,
            subject_band=profile.current_score.band.value,
        )
        return ReportView.from_report(report)