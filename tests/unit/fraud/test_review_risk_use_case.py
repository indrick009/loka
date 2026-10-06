"""ReviewRiskUseCase: the analyst-owned human-in-the-loop decisions."""

from __future__ import annotations

import uuid

import pytest
from fakes import FakeFraudFacts, FakeReportRepository, FakeRiskProfileRepository, FakeUnitOfWork

from loka.bounded_contexts.fraud.application.use_cases.evaluate_risk import (
    EvaluateRiskCommand,
    EvaluateRiskUseCase,
)
from loka.bounded_contexts.fraud.application.use_cases.review_risk import (
    ReviewRiskCommand,
    ReviewRiskDecision,
    ReviewRiskUseCase,
)
from loka.bounded_contexts.fraud.domain.entities.risk_profile import (
    Report,
    ReportReason,
    RiskSubject,
)
from loka.shared.domain.errors import ResourceNotFound, ValidationFailed

pytestmark = pytest.mark.unit

LANDLORD_ID = uuid.uuid4()
ACTOR_ID = uuid.uuid4()
HIGH_SCORE = 100  # unverified + 8 reports


def _now():
    from datetime import UTC, datetime

    return datetime(2026, 3, 1, 9, 0, tzinfo=UTC)


def _uow() -> FakeUnitOfWork:
    reports = FakeReportRepository()
    return FakeUnitOfWork(
        FakeRiskProfileRepository(), reports, FakeFraudFacts(reports)
    )


async def _score(uow: FakeUnitOfWork, *, reports: int, verification: str) -> None:
    for _ in range(reports):
        uow.reports.seed(
            Report(
                report_id=uuid.uuid4(),
                reporter_id=uuid.uuid4(),
                reason=ReportReason.SCAM,
                description="Scam behaviour",
                target_type="LANDLORD",
                target_id=LANDLORD_ID,
                now=_now(),
            )
        )
    uow.facts.verifications[LANDLORD_ID] = verification
    await EvaluateRiskUseCase(uow).execute(
        EvaluateRiskCommand(subject=RiskSubject.LANDLORD, subject_id=LANDLORD_ID),
        now=_now(),
    )


class TestReviewRisk:
    async def test_clearing_requires_a_note(self) -> None:
        uow = _uow()
        await _score(uow, reports=1, verification="PENDING")
        profile = await uow.profiles.get_by_subject("LANDLORD", LANDLORD_ID)
        use_case = ReviewRiskUseCase(uow)

        with pytest.raises(ValidationFailed, match="note is required"):
            await use_case.execute(
                ReviewRiskCommand(
                    profile_id=profile.profile_id,  # type: ignore[union-attr]
                    decision=ReviewRiskDecision.CLEAR,
                    actor_id=ACTOR_ID,
                ),
                now=_now(),
            )
        assert uow.commits == 1

    async def test_clearing_resets_score_and_marker(self) -> None:
        uow = _uow()
        await _score(uow, reports=8, verification="PENDING")
        profile = await uow.profiles.get_by_subject("LANDLORD", LANDLORD_ID)
        assert profile is not None and profile.current_score.score == HIGH_SCORE
        assert profile.under_manual_review is True

        use_case = ReviewRiskUseCase(uow)
        view = await use_case.execute(
            ReviewRiskCommand(
                profile_id=profile.profile_id,
                decision=ReviewRiskDecision.CLEAR,
                actor_id=ACTOR_ID,
                note="Identity documents verified over the phone",
            ),
            now=_now(),
        )

        assert view.score == 0
        assert view.band == "LOW"
        assert view.recommended_action == "NONE"
        assert view.under_manual_review is False
        assert view.restricted_until is None
        assert uow.commits == 2

    async def test_lifting_a_restriction_reopens_the_account(self) -> None:
        uow = _uow()
        await _score(uow, reports=8, verification="PENDING")
        profile = await uow.profiles.get_by_subject("LANDLORD", LANDLORD_ID)
        assert profile is not None and profile.restricted_until is not None

        use_case = ReviewRiskUseCase(uow)
        view = await use_case.execute(
            ReviewRiskCommand(
                profile_id=profile.profile_id,
                decision=ReviewRiskDecision.LIFT,
                actor_id=ACTOR_ID,
            ),
            now=_now(),
        )

        assert view.restricted_until is None

    async def test_lifting_a_restriction_that_does_not_exist_is_a_noop(self) -> None:
        uow = _uow()
        await _score(uow, reports=0, verification="VERIFIED")
        profile = await uow.profiles.get_by_subject("LANDLORD", LANDLORD_ID)
        use_case = ReviewRiskUseCase(uow)

        view = await use_case.execute(
            ReviewRiskCommand(
                profile_id=profile.profile_id,  # type: ignore[union-attr]
                decision=ReviewRiskDecision.LIFT,
                actor_id=ACTOR_ID,
            ),
            now=_now(),
        )

        assert view.restricted_until is None
        assert uow.commits == 2  # the _score helper evaluates first

    async def test_an_unknown_profile_is_not_found(self) -> None:
        uow = _uow()
        use_case = ReviewRiskUseCase(uow)

        with pytest.raises(ResourceNotFound, match="risk profile not found"):
            await use_case.execute(
                ReviewRiskCommand(
                    profile_id=uuid.uuid4(),
                    decision=ReviewRiskDecision.LIFT,
                    actor_id=ACTOR_ID,
                ),
                now=_now(),
            )
        assert uow.commits == 0
