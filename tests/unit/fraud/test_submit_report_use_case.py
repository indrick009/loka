"""SubmitReportUseCase: the human signal feeds the machine signals."""

from __future__ import annotations

import uuid

import pytest
from fakes import FakeFraudFacts, FakeReportRepository, FakeRiskProfileRepository, FakeUnitOfWork

from loka.bounded_contexts.fraud.application.use_cases.submit_report import (
    SubmitReportCommand,
    SubmitReportUseCase,
)
from loka.bounded_contexts.fraud.domain.entities.risk_profile import (
    Report,
    ReportReason,
    RiskProfile,
)
from loka.shared.domain.errors import ValidationFailed

pytestmark = pytest.mark.unit

LANDLORD_ID = uuid.uuid4()
REPORTER_ID = uuid.uuid4()


def _now():
    from datetime import UTC, datetime

    return datetime(2026, 3, 1, 9, 0, tzinfo=UTC)


def _uow() -> FakeUnitOfWork:
    reports = FakeReportRepository()
    return FakeUnitOfWork(
        FakeRiskProfileRepository(), reports, FakeFraudFacts(reports)
    )


class TestSubmitReport:
    async def test_a_report_on_an_unverified_landlord_escalates_immediately(self) -> None:
        uow = _uow()
        use_case = SubmitReportUseCase(uow)
        uow.facts.verifications[LANDLORD_ID] = "PENDING"

        view = await use_case.execute(
            SubmitReportCommand(
                reporter_id=REPORTER_ID,
                target_type="LANDLORD",
                target_id=LANDLORD_ID,
                reason=ReportReason.SCAM,
                description="Asking for a deposit before showing the flat",
            ),
            now=_now(),
        )

        assert view.status == "OPEN"
        assert view.target_type == "LANDLORD"
        assert len(uow.reports.added) == 1
        assert uow.commits == 1
        assert any(isinstance(item, Report) for item in uow.collected)
        assert any(isinstance(item, RiskProfile) for item in uow.collected)

        profile = await uow.profiles.get_by_subject("LANDLORD", LANDLORD_ID)
        assert profile is not None
        assert profile.current_score.score == 70
        assert profile.current_score.band.value == "HIGH"
        assert profile.under_manual_review is True

    async def test_an_unknown_target_type_is_refused(self) -> None:
        uow = _uow()
        use_case = SubmitReportUseCase(uow)

        with pytest.raises(ValidationFailed, match="target must be one of"):
            await use_case.execute(
                SubmitReportCommand(
                    reporter_id=REPORTER_ID,
                    target_type="BANANA",
                    target_id=LANDLORD_ID,
                    reason=ReportReason.SCAM,
                    description="Spam",
                ),
                now=_now(),
            )
        assert uow.commits == 0
        assert uow.reports.added == []

    async def test_an_empty_description_is_refused(self) -> None:
        uow = _uow()
        use_case = SubmitReportUseCase(uow)

        with pytest.raises(ValidationFailed, match="description is required"):
            await use_case.execute(
                SubmitReportCommand(
                    reporter_id=REPORTER_ID,
                    target_type="LANDLORD",
                    target_id=LANDLORD_ID,
                    reason=ReportReason.SCAM,
                    description="   ",
                ),
                now=_now(),
            )
        assert uow.commits == 0
