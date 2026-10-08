"""Rule that decides whether an AI verdict may stand alone.

The model proposes; the domain disposes. A model may only *approve* on its own,
and only when it is confident and the account carries little risk. A rejection
is never automated: refusing someone's identity on a model's word alone is not
a decision this platform is willing to take without a human. Everything the
rule turns down falls back to the manual queue, which is the safe default.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum

from loka.shared.domain.errors import InvariantViolation


class AdjudicationDecision(StrEnum):
    """What the model thinks of the submitted evidence."""

    APPROVE = "APPROVE"
    REJECT = "REJECT"
    UNCERTAIN = "UNCERTAIN"


class ReviewRoute(StrEnum):
    """Where the domain sends a verdict."""

    AUTO_APPROVE = "AUTO_APPROVE"
    MANUAL = "MANUAL"


@dataclass(frozen=True, slots=True)
class Adjudication:
    decision: AdjudicationDecision
    confidence: float
    reason: str

    def __post_init__(self) -> None:
        if not 0.0 <= self.confidence <= 1.0:
            raise InvariantViolation(
                "adjudication confidence must be between 0 and 1",
                context={"confidence": self.confidence},
            )


class VerificationAdjudicationPolicy:
    """Thresholds for letting an AI approval through unsupervised.

    Both bounds are deliberately conservative. Raising them is a product
    decision about how much risk the platform absorbs without a human; the
    numbers are named here so that decision is visible.
    """

    REQUIRED_CONFIDENCE = 0.9
    MAX_AUTO_RISK_SCORE = 40

    def route(self, adjudication: Adjudication, *, risk_score: int | None) -> ReviewRoute:
        if adjudication.decision is not AdjudicationDecision.APPROVE:
            return ReviewRoute.MANUAL
        if adjudication.confidence < self.REQUIRED_CONFIDENCE:
            return ReviewRoute.MANUAL
        if risk_score is not None and risk_score > self.MAX_AUTO_RISK_SCORE:
            return ReviewRoute.MANUAL
        return ReviewRoute.AUTO_APPROVE