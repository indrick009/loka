"""EvaluateRiskUseCase: scoring a subject into a persisted risk profile."""

from __future__ import annotations

import uuid

import pytest
from fakes import FakeFraudFacts, FakeReportRepository, FakeRiskProfileRepository, FakeUnitOfWork

from loka.bounded_contexts.fraud.application.use_cases.evaluate_risk import (
    EvaluateRiskCommand,
    EvaluateRiskUseCase,
)
from loka.bounded_contexts.fraud.domain.entities.risk_profile import RiskSubject
from loka.shared.domain.errors import ResourceNotFound, ValidationFailed

pytestmark = pytest.mark.unit

LANDLORD_ID = uuid.uuid4()
USER_ID = uuid.uuid4()
PROPERTY_ID = uuid.uuid4()


def _uow() -> FakeUnitOfWork:
    reports = FakeReportRepository()
    return FakeUnitOfWork(
        FakeRiskProfileRepository(), reports, FakeFraudFacts(reports)
    )


class TestEvaluateLandlord:
    async def test_unverified_landlord_reaches_medium_with_reasons(self) -> None:
        uow = _uow()
        use_case = EvaluateRiskUseCase(uow)
        uow.facts.verifications[LANDLORD_ID] = "PENDING"

        view = await use_case.execute(
            EvaluateRiskCommand(subject=RiskSubject.LANDLORD, subject_id=LANDLORD_ID),
            now=_now(),
        )

        assert view.band == "MEDIUM"
        assert view.score == 40
        assert view.signals[0].reason == "IDENTITY_UNVERIFIED"
        assert view.under_manual_review is False
        assert uow.commits == 1

    async def test_open_reports_escalate_to_manual_review_and_restriction(self) -> None:
        uow = _uow()
        use_case = EvaluateRiskUseCase(uow)
        uow.facts.verifications[LANDLORD_ID] = "PENDING"
        for _ in range(8):
            uow.reports.seed(_report(target_type="LANDLORD", target_id=LANDLORD_ID))

        view = await use_case.execute(
            EvaluateRiskCommand(subject=RiskSubject.LANDLORD, subject_id=LANDLORD_ID),
            now=_now(),
        )

        assert view.score == 100
        assert view.band == "CRITICAL"
        assert view.recommended_action == "TEMPORARY_RESTRICTION"
        assert view.under_manual_review is True
        assert view.restricted_until is not None

    async def test_re_evaluation_recomputes_and_quietens(self) -> None:
        uow = _uow()
        use_case = EvaluateRiskUseCase(uow)
        uow.facts.verifications[LANDLORD_ID] = "PENDING"
        first = await use_case.execute(
            EvaluateRiskCommand(subject=RiskSubject.LANDLORD, subject_id=LANDLORD_ID),
            now=_now(),
        )
        uow.facts.verifications[LANDLORD_ID] = "VERIFIED"

        second = await use_case.execute(
            EvaluateRiskCommand(subject=RiskSubject.LANDLORD, subject_id=LANDLORD_ID),
            now=_now(),
        )

        assert first.score == 40
        assert second.score == 0
        assert second.band == "LOW"
        assert len(uow.profiles.profiles) == 1


class TestEvaluateUser:
    async def test_repeated_rejections_score_severe(self) -> None:
        uow = _uow()
        use_case = EvaluateRiskUseCase(uow)
        uow.facts.users[USER_ID] = ("hash-a", 1)
        uow.facts.failed_payments[USER_ID] = 5

        view = await use_case.execute(
            EvaluateRiskCommand(subject=RiskSubject.USER, subject_id=USER_ID), now=_now()
        )

        assert view.score == 60
        assert view.band == "MEDIUM"

    async def test_phone_reuse_lifts_across_the_medium_threshold(self) -> None:
        uow = _uow()
        use_case = EvaluateRiskUseCase(uow)
        uow.facts.users[USER_ID] = ("hash-a", 2)
        uow.facts.failed_payments[USER_ID] = 5

        view = await use_case.execute(
            EvaluateRiskCommand(subject=RiskSubject.USER, subject_id=USER_ID), now=_now()
        )

        assert view.score == 100
        assert view.band == "CRITICAL"
        assert view.restricted_until is not None


class TestEvaluateProperty:
    async def test_a_missing_property_is_not_found(self) -> None:
        uow = _uow()
        use_case = EvaluateRiskUseCase(uow)

        with pytest.raises(ResourceNotFound, match="property not found"):
            await use_case.execute(
                EvaluateRiskCommand(
                    subject=RiskSubject.PROPERTY, subject_id=uuid.uuid4()
                ),
                now=_now(),
            )

    async def test_price_anomaly_is_signalled(self) -> None:
        uow = _uow()
        use_case = EvaluateRiskUseCase(uow)
        uow.facts.properties[PROPERTY_ID] = _property_facts()

        view = await use_case.execute(
            EvaluateRiskCommand(subject=RiskSubject.PROPERTY, subject_id=PROPERTY_ID),
            now=_now(),
        )

        assert view.score == 30
        assert view.signals[0].reason == "PRICE_ANOMALY"


class TestConversationNotWired:
    async def test_conversation_subjects_are_refused(self) -> None:
        uow = _uow()
        use_case = EvaluateRiskUseCase(uow)

        with pytest.raises(ValidationFailed, match="not wired"):
            await use_case.execute(
                EvaluateRiskCommand(
                    subject=RiskSubject.CONVERSATION, subject_id=uuid.uuid4()
                ),
                now=_now(),
            )
        assert uow.commits == 0


def _now():
    from datetime import UTC, datetime

    return datetime(2026, 3, 1, 9, 0, tzinfo=UTC)


def _report(*, target_type: str, target_id: uuid.UUID):
    from loka.bounded_contexts.fraud.domain.entities.risk_profile import (
        Report,
        ReportReason,
    )

    return Report(
        report_id=uuid.uuid4(),
        reporter_id=uuid.uuid4(),
        reason=ReportReason.SCAM,
        description="Suspicious behaviour",
        target_type=target_type,
        target_id=target_id,
        now=_now(),
    )


def _property_facts():
    from loka.bounded_contexts.fraud.domain.value_objects.facts import PropertyFacts

    return PropertyFacts(
        property_id=PROPERTY_ID,
        landlord_id=LANDLORD_ID,
        status="AVAILABLE",
        city="Yaoundé",
        rent_xaf=100_000,
        public_price_xaf=40_000,
        other_landlords_sharing_media=0,
        open_report_count=0,
        city_median_price_xaf=100_000,
    )
