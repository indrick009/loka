"""Aggregated fraud telemetry for the analyst console."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime

from loka.bounded_contexts.fraud.application.dto.fraud_dto import FraudSummaryView
from loka.shared.application.unit_of_work import UnitOfWork
from loka.shared.application.use_case import UseCase

PROFILES_NAME = "fraud_profile"
REPORTS_NAME = "fraud_report"


@dataclass(frozen=True, slots=True)
class FraudSummaryQuery:
    pass


class GetFraudSummaryUseCase(UseCase[FraudSummaryQuery, FraudSummaryView]):
    name = "fraud.get_summary"

    def __init__(self, uow: UnitOfWork) -> None:
        super().__init__(uow)
        self._profiles = uow.repository(PROFILES_NAME)
        self._reports = uow.repository(REPORTS_NAME)

    async def execute(  # type: ignore[override]
        self, command: FraudSummaryQuery, *, now: datetime
    ) -> FraudSummaryView:
        bands = await self._profiles.count_by_band()
        reasons = await self._profiles.active_signal_reasons(limit=10)
        report_counts = await self._reports.count_by_status()
        total = sum(bands.values())
        return FraudSummaryView(
            total_profiles=total,
            bands=bands,
            top_reasons=[
                {"reason": str(reason), "count": count} for reason, count in reasons
            ],
            open_reports=report_counts,
        )