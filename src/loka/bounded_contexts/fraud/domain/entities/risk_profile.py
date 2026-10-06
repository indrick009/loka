"""Fraud risk model.

Design constraint from the product spec: a risk score is a *recommendation*,
never an automatic ban. Four bands map to four human-in-the-loop responses.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import datetime, timedelta
from enum import StrEnum

from loka.shared.domain.aggregate import AggregateRoot
from loka.shared.domain.clock import ensure_utc
from loka.shared.domain.errors import InvariantViolation
from loka.shared.domain.value_object import ValueObject


@dataclass(frozen=True, slots=True)
class RiskScore(ValueObject):
    """Integer 0-100. Scores are recomputed from signals, never incremented blindly."""

    score: int
    reasons: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        if not isinstance(self.score, int) or isinstance(self.score, bool):
            self._reject("risk score must be an integer", score=self.score)
        if not 0 <= self.score <= 100:
            self._reject("risk score must be within 0..100", score=self.score)

    @property
    def band(self) -> RiskBand:
        if self.score >= 90:
            return RiskBand.CRITICAL
        if self.score >= 70:
            return RiskBand.HIGH
        if self.score >= 40:
            return RiskBand.MEDIUM
        return RiskBand.LOW

    def to_primitive(self) -> dict[str, object]:
        return {"score": self.score, "band": self.band.value, "reasons": list(self.reasons)}


class RiskBand(StrEnum):
    """Severity bands. Ordering comes from ``rank``, never from alphabetical
    comparison of the string values."""

    LOW = "LOW"
    MEDIUM = "MEDIUM"
    HIGH = "HIGH"
    CRITICAL = "CRITICAL"

    @property
    def rank(self) -> int:
        return _BAND_RANK[self]

    def __lt__(self, other: object) -> bool:
        if not isinstance(other, RiskBand):
            return NotImplemented
        return self.rank < other.rank

    def __le__(self, other: object) -> bool:
        if not isinstance(other, RiskBand):
            return NotImplemented
        return self.rank <= other.rank

    def __gt__(self, other: object) -> bool:
        if not isinstance(other, RiskBand):
            return NotImplemented
        return self.rank > other.rank

    def __ge__(self, other: object) -> bool:
        if not isinstance(other, RiskBand):
            return NotImplemented
        return self.rank >= other.rank


_BAND_RANK: dict[RiskBand, int] = {
    RiskBand.LOW: 0,
    RiskBand.MEDIUM: 1,
    RiskBand.HIGH: 2,
    RiskBand.CRITICAL: 3,
}


class RecommendedAction(StrEnum):
    NONE = "NONE"
    ADDITIONAL_VERIFICATION = "ADDITIONAL_VERIFICATION"
    MONITOR = "MONITOR"
    MANUAL_REVIEW = "MANUAL_REVIEW"
    TEMPORARY_RESTRICTION = "TEMPORARY_RESTRICTION"


_BAND_ACTIONS: dict[RiskBand, RecommendedAction] = {
    RiskBand.LOW: RecommendedAction.NONE,
    RiskBand.MEDIUM: RecommendedAction.ADDITIONAL_VERIFICATION,
    RiskBand.HIGH: RecommendedAction.MANUAL_REVIEW,
    RiskBand.CRITICAL: RecommendedAction.TEMPORARY_RESTRICTION,
}


def action_for(band: RiskBand) -> RecommendedAction:
    return _BAND_ACTIONS[band]


class RiskReason(StrEnum):
    IDENTITY_UNVERIFIED = "IDENTITY_UNVERIFIED"
    MULTIPLE_ACCOUNTS = "MULTIPLE_ACCOUNTS"
    PHONE_REUSE = "PHONE_REUSE"
    FREQUENT_CHANGES = "FREQUENT_CHANGES"
    LISTING_REMOVED = "LISTING_REMOVED"
    LISTING_REPORTED = "LISTING_REPORTED"
    PRICE_ANOMALY = "PRICE_ANOMALY"
    DUPLICATE_PHOTOS = "DUPLICATE_PHOTOS"
    SHARED_CONTACT_DETAILS = "SHARED_CONTACT_DETAILS"
    OFF_PLATFORM_PAYMENT_ATTEMPT = "OFF_PLATFORM_PAYMENT_ATTEMPT"
    CONTACT_REVEAL_BEFORE_PAYMENT = "CONTACT_REVEAL_BEFORE_PAYMENT"
    INCONSISTENT_INFORMATION = "INCONSISTENT_INFORMATION"
    LOCATION_DRIFT = "LOCATION_DRIFT"
    AVAILABILITY_MISREPORTED = "AVAILABILITY_MISREPORTED"
    MULTIPLE_REJECTIONS = "MULTIPLE_REJECTIONS"
    EXTERNAL_ACQUISITION = "EXTERNAL_ACQUISITION"


class RiskSubject(StrEnum):
    USER = "USER"
    LANDLORD = "LANDLORD"
    PROPERTY = "PROPERTY"
    CONVERSATION = "CONVERSATION"


@dataclass(frozen=True, slots=True)
class RiskSignal:
    reason: RiskReason
    weight: int
    detail: str | None = None

    def __post_init__(self) -> None:
        if not 0 < self.weight <= 100:
            raise InvariantViolation("signal weight must be within 1..100")


class RiskProfile(AggregateRoot):
    __slots__ = (
        "created_at",
        "current_score",
        "last_evaluated_at",
        "profile_id",
        "recommended_action",
        "restricted_until",
        "signals",
        "subject",
        "subject_id",
        "under_manual_review",
        "updated_at",
    )

    def __init__(
        self,
        *,
        profile_id: uuid.UUID,
        subject: RiskSubject,
        subject_id: uuid.UUID,
        now: datetime,
    ) -> None:
        super().__init__(aggregate_type="RiskProfile")
        self._assign_id(profile_id)
        self.profile_id = profile_id
        self.subject = subject
        self.subject_id = subject_id
        self.current_score = RiskScore(score=0)
        self.signals: list[RiskSignal] = []
        self.recommended_action = RecommendedAction.NONE
        self.under_manual_review = False
        self.restricted_until: datetime | None = None
        self.last_evaluated_at: datetime | None = None
        self.created_at = ensure_utc(now)
        self.updated_at = ensure_utc(now)

    @property
    def band(self) -> RiskBand:
        return self.current_score.band

    @property
    def is_restricted(self) -> bool:
        return self.restricted_until is not None

    def apply_signals(
        self, signals: list[RiskSignal], *, now: datetime, actor_id: uuid.UUID | None = None
    ) -> RiskProfile:
        """Recompute the score from the currently active signals.

        Recomputing instead of incrementing keeps the score explainable: the
        same evidence always yields the same score.
        """
        if not signals:
            self.current_score = RiskScore(score=0, reasons=())
            self.signals = []
            self.recommended_action = RecommendedAction.NONE
            self.last_evaluated_at = ensure_utc(now)
            self.updated_at = ensure_utc(now)
            self._bump()
            self.record(
                "FraudRiskScoreUpdated",
                occurred_at=now,
                actor_id=actor_id,
                payload={
                    "profile_id": str(self.id),
                    "subject": self.subject.value,
                    "subject_id": str(self.subject_id),
                    "score": 0,
                    "band": RiskBand.LOW.value,
                    "reasons": [],
                    "recommended_action": self.recommended_action.value,
                },
            )
            return self

        weight = sum(signal.weight for signal in signals)
        score = min(weight, 100)
        reasons = tuple(sorted({signal.reason.value for signal in signals}))
        previous_band = self.current_score.band

        self.current_score = RiskScore(score=score, reasons=reasons)
        self.signals = signals
        self.recommended_action = action_for(self.current_score.band)
        self.last_evaluated_at = ensure_utc(now)
        self.updated_at = ensure_utc(now)
        self._bump()

        if self.current_score.band >= RiskBand.HIGH and previous_band < RiskBand.HIGH:
            self.under_manual_review = True
        if self.current_score.band == RiskBand.CRITICAL:
            self.restricted_until = ensure_utc(now) + timedelta(days=7)

        self.record(
            "FraudRiskScoreUpdated",
            occurred_at=now,
            actor_id=actor_id,
            payload={
                "profile_id": str(self.id),
                "subject": self.subject.value,
                "subject_id": str(self.subject_id),
                "score": score,
                "band": self.current_score.band.value,
                "reasons": list(reasons),
                "recommended_action": self.recommended_action.value,
            },
        )
        if self.current_score.band != previous_band:
            self.record(
                "FraudRiskDetected",
                occurred_at=now,
                actor_id=actor_id,
                payload={
                    "profile_id": str(self.id),
                    "subject": self.subject.value,
                    "subject_id": str(self.subject_id),
                    "band": self.current_score.band.value,
                    "reasons": list(reasons),
                    "recommended_action": self.recommended_action.value,
                },
            )
        return self

    def clear_signals(self, *, now: datetime, note: str, actor_id: uuid.UUID) -> None:
        self.signals = []
        self.current_score = RiskScore(score=0, reasons=())
        self.recommended_action = RecommendedAction.NONE
        self.under_manual_review = False
        self.restricted_until = None
        self.last_evaluated_at = ensure_utc(now)
        self.updated_at = ensure_utc(now)
        self._bump()
        self.record(
            "FraudRiskCleared",
            occurred_at=now,
            actor_id=actor_id,
            payload={"profile_id": str(self.id), "note": note},
        )

    def lift_restriction(self, *, now: datetime, actor_id: uuid.UUID) -> None:
        if self.restricted_until is None:
            return
        self.restricted_until = None
        self._touch(now)
        self.record(
            "FraudRestrictionLifted",
            occurred_at=now,
            actor_id=actor_id,
            payload={"profile_id": str(self.id)},
        )

    def assert_not_restricted(self, *, action: str) -> None:
        """Only temporary restrictions block activity. Never a hard ban."""
        if self.restricted_until and ensure_utc(datetime.now()) < self.restricted_until:
            self._reject(
                f"{action} is temporarily restricted pending review",
                until=self.restricted_until.isoformat(),
            )

    def _touch(self, now: datetime) -> None:
        self.updated_at = ensure_utc(now)
        self._bump()


class ReportReason(StrEnum):
    FAKE_PROPERTY = "FAKE_PROPERTY"
    FAKE_LANDLORD = "FAKE_LANDLORD"
    WRONG_PRICE = "WRONG_PRICE"
    UNAVAILABLE_PROPERTY = "UNAVAILABLE_PROPERTY"
    MISLEADING_PHOTOS = "MISLEADING_PHOTOS"
    HIDDEN_FEE = "HIDDEN_FEE"
    HARASSMENT = "HARASSMENT"
    SCAM = "SCAM"
    DUPLICATE_LISTING = "DUPLICATE_LISTING"
    OFF_PLATFORM_PAYMENT = "OFF_PLATFORM_PAYMENT"
    OTHER = "OTHER"


class ReportStatus(StrEnum):
    OPEN = "OPEN"
    INVESTIGATING = "INVESTIGATING"
    DISMISSED = "DISMISSED"
    ACTIONED = "ACTIONED"


class Report(AggregateRoot):
    __slots__ = (
        "assigned_to",
        "created_at",
        "description",
        "evidence",
        "reason",
        "report_id",
        "reporter_id",
        "resolution",
        "status",
        "target_id",
        "target_type",
        "updated_at",
    )

    def __init__(
        self,
        *,
        report_id: uuid.UUID,
        reporter_id: uuid.UUID,
        reason: ReportReason,
        description: str,
        target_type: str,
        target_id: uuid.UUID,
        now: datetime,
        evidence: list[str] | None = None,
    ) -> None:
        super().__init__(aggregate_type="Report")
        self._assign_id(report_id)
        self.report_id = report_id
        self.reporter_id = reporter_id
        self.reason = reason
        self.description = description.strip()
        self.target_type = target_type
        self.target_id = target_id
        self.status: ReportStatus = ReportStatus.OPEN
        self.evidence: list[str] = evidence or []
        self.assigned_to: uuid.UUID | None = None
        self.resolution: str | None = None
        self.created_at = ensure_utc(now)
        self.updated_at = ensure_utc(now)

    def investigate(self, *, analyst_id: uuid.UUID, now: datetime) -> None:
        if self.status is not ReportStatus.OPEN:
            self._reject("only an open report can be assigned", status=self.status.value)
        self.status = ReportStatus.INVESTIGATING
        self.assigned_to = analyst_id
        self._touch(now)
        self.record(
            "ReportInvestigating",
            occurred_at=now,
            actor_id=analyst_id,
            payload={"report_id": str(self.id), "target_id": str(self.target_id)},
        )

    def dismiss(self, *, analyst_id: uuid.UUID, resolution: str, now: datetime) -> None:
        if self.status is ReportStatus.ACTIONED:
            self._reject("report already actioned")
        self.status = ReportStatus.DISMISSED
        self.resolution = resolution
        self._touch(now)
        self.record(
            "ReportDismissed",
            occurred_at=now,
            actor_id=analyst_id,
            payload={"report_id": str(self.id), "resolution": resolution},
        )

    def mark_actioned(self, *, analyst_id: uuid.UUID, resolution: str, now: datetime) -> None:
        if self.status is ReportStatus.DISMISSED:
            self._reject("a dismissed report cannot be actioned")
        self.status = ReportStatus.ACTIONED
        self.resolution = resolution
        self._touch(now)
        self.record(
            "ReportActioned",
            occurred_at=now,
            actor_id=analyst_id,
            payload={"report_id": str(self.id), "resolution": resolution},
        )

    def _touch(self, now: datetime) -> None:
        self.updated_at = ensure_utc(now)
        self._bump()