"""ReviewReportUseCase: analyst transitions, always in agreement with the risk state."""

from __future__ import annotations

import uuid

import pytest
from fakes import FakeFraudFacts, FakeReportRepository, FakeRiskProfileRepository, FakeUnitOfWork

from loka.bounded_contexts.fraud.application.use_cases.evaluate_risk import (
    EvaluateRiskCommand,
    EvaluateRiskUseCase,
)
from loka.bounded_contexts.fraud.application.use_cases.review_report import (
    ReviewReportAction,
    ReviewReportCommand,
    ReviewReportUseCase,
)
from loka.bounded_contexts.fraud.domain.entities.risk_profile import (
    Report,
    ReportReason,
    RiskProfile,
    RiskSubject,
)
from loka.shared.domain.errors import ResourceNotFound, ValidationFailed

pytestmark = pytest.mark.unit

LANDLORD_ID = uuid.uuid4()
ANALYST_ID = uuid.uuid4()


def _now():
    from datetime import UTC, datetime

    return datetime(2026, 3, 1, 9, 0, tzinfo=UTC)


def _uow() -> FakeUnitOfWork:
    reports = FakeReportRepository()
    return FakeUnitOfWork(
        FakeRiskProfileRepository(), reports, FakeFraudFacts(reports)
    )


def _report() -> Report:
    return Report(
        report_id=uuid.uuid4(),
        reporter_id=uuid.uuid4(),
        reason=ReportReason.FAKE_LANDLORD,
        description="No such landlord",
        target_type="LANDLORD",
        target_id=LANDLORD_ID,
        now=_now(),
    )


class TestReviewReport:
    async def test_an_investigation_assigns_the_analyst_and_re_evaluates(self) -> None:
        uow = _uow()
        use_case = ReviewReportUseCase(uow)
        report = _report()
        uow.reports.seed(report)

        view = await use_case.execute(
            ReviewReportCommand(
                report_id=report.report_id,
                action=ReviewReportAction.INVESTIGATE,
                analyst_id=ANALYST_ID,
            ),
            now=_now(),
        )

        assert view.status == "INVESTIGATING"
        assert view.assigned_to == ANALYST_ID
        assert uow.commits == 1
        assert any(isinstance(item, RiskProfile) for item in uow.collected)

    async def test_dismissing_requires_a_resolution(self) -> None:
        uow = _uow()
        use_case = ReviewReportUseCase(uow)
        report = _report()
        uow.reports.seed(report)

        with pytest.raises(ValidationFailed, match="resolution is required"):
            await use_case.execute(
                ReviewReportCommand(
                    report_id=report.report_id,
                    action=ReviewReportAction.DISMISS,
                    analyst_id=ANALYST_ID,
                ),
                now=_now(),
            )
        assert uow.commits == 0

    async def test_dismissing_lowers_the_score_once_the_evidence_is_gone(self) -> None:
        uow = _uow()
        report = _report()
        uow.reports.seed(report)
        uow.facts.verifications[LANDLORD_ID] = "PENDING"
        await EvaluateRiskUseCase(uow).execute(
            EvaluateRiskCommand(
                subject=RiskSubject.LANDLORD, subject_id=LANDLORD_ID
            ),
            now=_now(),
        )

        use_case = ReviewReportUseCase(uow)
        await use_case.execute(
            ReviewReportCommand(
                report_id=report.report_id,
                action=ReviewReportAction.DISMISS,
                analyst_id=ANALYST_ID,
                resolution="Repeated phone checks showed a real agent",
            ),
            now=_now(),
        )

        assert (await uow.reports.get(report.report_id)) is not None
        reloaded = await uow.profiles.get_by_subject("LANDLORD", LANDLORD_ID)
        assert reloaded is not None
        assert reloaded.current_score.score == 40
        assert reloaded.current_score.band.value == "MEDIUM"

    async def test_actioning_closes_the_report(self) -> None:
        uow = _uow()
        use_case = ReviewReportUseCase(uow)
        report = _report()
        uow.reports.seed(report)

        view = await use_case.execute(
            ReviewReportCommand(
                report_id=report.report_id,
                action=ReviewReportAction.ACTION,
                analyst_id=ANALYST_ID,
                resolution="Passed to identity verification and outreach",
            ),
            now=_now(),
        )

        assert view.status == "ACTIONED"
        assert view.resolution == "Passed to identity verification and outreach"

    async def test_an_unknown_report_is_not_found(self) -> None:
        uow = _uow()
        use_case = ReviewReportUseCase(uow)

        with pytest.raises(ResourceNotFound, match="report not found"):
            await use_case.execute(
                ReviewReportCommand(
                    report_id=uuid.uuid4(),
                    action=ReviewReportAction.INVESTIGATE,
                    analyst_id=ANALYST_ID,
                ),
                now=_now(),
            )
        assert uow.commits == 0
