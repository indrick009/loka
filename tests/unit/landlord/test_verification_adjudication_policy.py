"""The rule that decides whether a model may approve on its own."""

from __future__ import annotations

import pytest

from loka.bounded_contexts.landlord.domain.services.verification_adjudication import (
    Adjudication,
    AdjudicationDecision,
    ReviewRoute,
    VerificationAdjudicationPolicy,
)
from loka.shared.domain.errors import InvariantViolation

pytestmark = pytest.mark.unit


def _verdict(decision: AdjudicationDecision, confidence: float) -> Adjudication:
    return Adjudication(decision=decision, confidence=confidence, reason="r")


def test_a_confident_low_risk_approval_stands_alone() -> None:
    route = VerificationAdjudicationPolicy().route(
        _verdict(AdjudicationDecision.APPROVE, 0.97), risk_score=10
    )
    assert route is ReviewRoute.AUTO_APPROVE


def test_a_lukewarm_approval_goes_to_a_human() -> None:
    route = VerificationAdjudicationPolicy().route(
        _verdict(AdjudicationDecision.APPROVE, 0.8), risk_score=10
    )
    assert route is ReviewRoute.MANUAL


def test_a_high_risk_account_is_never_auto_approved() -> None:
    route = VerificationAdjudicationPolicy().route(
        _verdict(AdjudicationDecision.APPROVE, 0.99), risk_score=80
    )
    assert route is ReviewRoute.MANUAL


def test_a_model_rejection_is_not_automated() -> None:
    route = VerificationAdjudicationPolicy().route(
        _verdict(AdjudicationDecision.REJECT, 0.99), risk_score=0
    )
    assert route is ReviewRoute.MANUAL


def test_uncertain_always_escalates() -> None:
    route = VerificationAdjudicationPolicy().route(
        _verdict(AdjudicationDecision.UNCERTAIN, 0.99), risk_score=None
    )
    assert route is ReviewRoute.MANUAL


def test_an_out_of_range_confidence_is_refused() -> None:
    with pytest.raises(InvariantViolation):
        Adjudication(
            decision=AdjudicationDecision.APPROVE, confidence=1.5, reason="r"
        )