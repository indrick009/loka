"""Report listing for the analyst console."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime

from loka.bounded_contexts.fraud.application.dto.fraud_dto import ReportView
from loka.bounded_contexts.fraud.domain.entities.risk_profile import ReportStatus
from loka.shared.application.unit_of_work import UnitOfWork
from loka.shared.application.use_case import UseCase

REPOSITORY_NAME = "fraud_report"


@dataclass(frozen=True, slots=True)
class ReportListQuery:
    status: ReportStatus | None = None
    limit: int = 50
    offset: int = 0


class ListReportsUseCase(UseCase[ReportListQuery, list[ReportView]]):
    name = "fraud.list_reports"

    def __init__(self, uow: UnitOfWork) -> None:
        super().__init__(uow)
        self._reports = uow.repository(REPOSITORY_NAME)

    async def execute(  # type: ignore[override]
        self, command: ReportListQuery, *, now: datetime) -> list[ReportView]:
        reports = await self._reports.list(
            status=command.status,
            limit=command.limit,
            offset=command.offset,
        )
        return [ReportView.from_report(report) for report in reports]