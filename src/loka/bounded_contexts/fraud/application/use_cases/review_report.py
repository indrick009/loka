"""Analyst handling of reports.

Transitions are owned by the aggregate. Dismissing or actioning a report
changes the evidence base, so the target is re-evaluated in the same
transaction and the score follows the truth.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import datetime
from enum import StrEnum

from loka.bounded_contexts.fraud.application.dto.fraud_dto import ReportView
from loka.bounded_contexts.fraud.application.risk_evaluator import RiskEvaluator
from loka.bounded_contexts.fraud.domain.entities.risk_profile import (
    ReportStatus,
    RiskSubject,
)
from loka.shared.application.unit_of_work import UnitOfWork
from loka.shared.application.use_case import UseCase
from loka.shared.domain.errors import ResourceNotFound, ValidationFailed

REPORT_REPOSITORY_NAME = "fraud_report"

_REPORT_SUBJECTS: dict[str, RiskSubject] = {
    "PROPERTY": RiskSubject.PROPERTY,
    "LANDLORD": RiskSubject.LANDLORD,
    "USER": RiskSubject.USER,
}


class ReviewReportAction(StrEnum):
    INVESTIGATE = "INVESTIGATE"
    DISMISS = "DISMISS"
    ACTION = "ACTION"


@dataclass(frozen=True, slots=True)
class ReviewReportCommand:
    report_id: uuid.UUID
    action: ReviewReportAction
    analyst_id: uuid.UUID
    resolution: str | None = None


class ReviewReportUseCase(UseCase[ReviewReportCommand, ReportView]):
    name = "fraud.review_report"

    def __init__(self, uow: UnitOfWork) -> None:
        super().__init__(uow)
        self._reports = uow.repository(REPORT_REPOSITORY_NAME)
        self._evaluator = RiskEvaluator(uow)

    async def execute(  # type: ignore[override]
        self, command: ReviewReportCommand, *, now: datetime) -> ReportView:
        report = await self._reports.get(command.report_id)
        if report is None:
            raise ResourceNotFound(
                "report not found", context={"report_id": str(command.report_id)}
            )

        if command.action is ReviewReportAction.INVESTIGATE:
            report.investigate(analyst_id=command.analyst_id, now=now)
        else:
            if not command.resolution or not command.resolution.strip():
                raise ValidationFailed(
                    "a resolution is required to dismiss or action a report"
                )
            if command.action is ReviewReportAction.DISMISS:
                report.dismiss(
                    analyst_id=command.analyst_id,
                    resolution=command.resolution,
                    now=now,
                )
            else:
                report.mark_actioned(
                    analyst_id=command.analyst_id,
                    resolution=command.resolution,
                    now=now,
                )

        await self._reports.save(report)
        self._uow.collect(report)

        subject = _REPORT_SUBJECTS.get(report.target_type)
        if subject is not None and report.status is not ReportStatus.OPEN:
            profile = await self._evaluator.apply(subject, report.target_id, now)
            self._uow.collect(profile)

        await self._uow.commit()
        self._log(
            "report_reviewed",
            report_id=str(report.id),
            action=command.action.value,
            status=report.status.value,
            analyst_id=str(command.analyst_id),
        )
        return ReportView.from_report(report)