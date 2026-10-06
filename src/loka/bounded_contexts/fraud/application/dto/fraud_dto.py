"""Fraud context response DTOs.

Thin immutable projections for the HTTP layer; they never leak ORM rows or
aggregates upwards.
"""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any

from loka.bounded_contexts.fraud.domain.entities.risk_profile import (
    Report,
    RiskProfile,
    RiskSignal,
)


class RiskSignalView:
    __slots__ = ("detail", "reason", "weight")

    def __init__(self, *, reason: str, weight: int, detail: str | None) -> None:
        self.reason = reason
        self.weight = weight
        self.detail = detail

    @classmethod
    def from_signal(cls, signal: RiskSignal) -> RiskSignalView:
        return cls(reason=signal.reason.value, weight=signal.weight, detail=signal.detail)

    def to_dict(self) -> dict[str, Any]:
        return {"reason": self.reason, "weight": self.weight, "detail": self.detail}


class RiskProfileView:
    __slots__ = (
        "band",
        "last_evaluated_at",
        "profile_id",
        "reasons",
        "recommended_action",
        "restricted_until",
        "score",
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
        subject: str,
        subject_id: uuid.UUID,
        score: int,
        band: str,
        reasons: list[str],
        signals: list[RiskSignalView],
        recommended_action: str,
        under_manual_review: bool,
        restricted_until: datetime | None,
        last_evaluated_at: datetime | None,
        updated_at: datetime,
    ) -> None:
        self.profile_id = profile_id
        self.subject = subject
        self.subject_id = subject_id
        self.score = score
        self.band = band
        self.reasons = reasons
        self.signals = signals
        self.recommended_action = recommended_action
        self.under_manual_review = under_manual_review
        self.restricted_until = restricted_until
        self.last_evaluated_at = last_evaluated_at
        self.updated_at = updated_at

    @classmethod
    def from_profile(cls, profile: RiskProfile) -> RiskProfileView:
        return cls(
            profile_id=profile.profile_id,
            subject=profile.subject.value,
            subject_id=profile.subject_id,
            score=profile.current_score.score,
            band=profile.current_score.band.value,
            reasons=list(profile.current_score.reasons),
            signals=[RiskSignalView.from_signal(s) for s in profile.signals],
            recommended_action=profile.recommended_action.value,
            under_manual_review=profile.under_manual_review,
            restricted_until=profile.restricted_until,
            last_evaluated_at=profile.last_evaluated_at,
            updated_at=profile.updated_at,
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "profile_id": str(self.profile_id),
            "subject": self.subject,
            "subject_id": str(self.subject_id),
            "score": self.score,
            "band": self.band,
            "reasons": self.reasons,
            "signals": [signal.to_dict() for signal in self.signals],
            "recommended_action": self.recommended_action,
            "under_manual_review": self.under_manual_review,
            "restricted_until": (
                self.restricted_until.isoformat() if self.restricted_until else None
            ),
            "last_evaluated_at": (
                self.last_evaluated_at.isoformat() if self.last_evaluated_at else None
            ),
            "updated_at": self.updated_at.isoformat(),
        }


class ReportView:
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
        reason: str,
        description: str,
        target_type: str,
        target_id: uuid.UUID,
        status: str,
        evidence: list[str],
        assigned_to: uuid.UUID | None,
        resolution: str | None,
        created_at: datetime,
        updated_at: datetime,
    ) -> None:
        self.report_id = report_id
        self.reporter_id = reporter_id
        self.reason = reason
        self.description = description
        self.target_type = target_type
        self.target_id = target_id
        self.status = status
        self.evidence = evidence
        self.assigned_to = assigned_to
        self.resolution = resolution
        self.created_at = created_at
        self.updated_at = updated_at

    @classmethod
    def from_report(cls, report: Report) -> ReportView:
        return cls(
            report_id=report.report_id,
            reporter_id=report.reporter_id,
            reason=report.reason.value,
            description=report.description,
            target_type=report.target_type,
            target_id=report.target_id,
            status=report.status.value,
            evidence=list(report.evidence),
            assigned_to=report.assigned_to,
            resolution=report.resolution,
            created_at=report.created_at,
            updated_at=report.updated_at,
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "report_id": str(self.report_id),
            "reporter_id": str(self.reporter_id),
            "reason": self.reason,
            "description": self.description,
            "target_type": self.target_type,
            "target_id": str(self.target_id),
            "status": self.status,
            "evidence": self.evidence,
            "assigned_to": str(self.assigned_to) if self.assigned_to else None,
            "resolution": self.resolution,
            "created_at": self.created_at.isoformat(),
            "updated_at": self.updated_at.isoformat(),
        }


class EscalationQueueView:
    __slots__ = ("items", "total")

    def __init__(self, *, items: list[RiskProfileView], total: int) -> None:
        self.items = items
        self.total = total

    def to_dict(self) -> dict[str, Any]:
        return {
            "total": self.total,
            "items": [item.to_dict() for item in self.items],
        }


class FraudSummaryView:
    __slots__ = ("bands", "open_reports", "top_reasons", "total_profiles")

    def __init__(
        self,
        *,
        total_profiles: int,
        bands: dict[str, int],
        top_reasons: list[dict[str, Any]],
        open_reports: dict[str, int],
    ) -> None:
        self.total_profiles = total_profiles
        self.bands = bands
        self.top_reasons = top_reasons
        self.open_reports = open_reports

    def to_dict(self) -> dict[str, Any]:
        return {
            "total_profiles": self.total_profiles,
            "bands": self.bands,
            "top_reasons": self.top_reasons,
            "open_reports": self.open_reports,
        }