"""Landlord verification DTOs.

Application-layer shapes returned to interfaces. They expose neither ORM rows
nor mutable aggregates, and deliberately never surface a document object key:
identity evidence stays behind the storage layer.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any

from loka.bounded_contexts.landlord.domain.entities.landlord_profile import LandlordProfile
from loka.bounded_contexts.landlord.domain.entities.verification_request import (
    VerificationRequest,
)


@dataclass(frozen=True, slots=True)
class LandlordVerificationView:
    request_id: str
    landlord_id: str
    user_id: str
    status: str
    review_mode: str
    rejection_reason: str | None
    risk_score: int | None
    submitted_at: str | None
    decided_at: str | None
    document_count: int
    missing_evidence: list[str] = field(default_factory=list)
    can_publish: bool = False

    @classmethod
    def from_aggregates(
        cls, request: VerificationRequest, profile: LandlordProfile
    ) -> LandlordVerificationView:
        return cls(
            request_id=str(request.request_id),
            landlord_id=str(request.landlord_id),
            user_id=str(request.user_id),
            status=request.status.value,
            review_mode=request.review_mode.value,
            rejection_reason=(
                request.rejection_reason.value if request.rejection_reason else None
            ),
            risk_score=request.risk_score,
            submitted_at=request.submitted_at.isoformat() if request.submitted_at else None,
            decided_at=request.decided_at.isoformat() if request.decided_at else None,
            document_count=len(request.documents),
            missing_evidence=request.missing_evidence(),
            can_publish=profile.can_publish_property,
        )

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True, slots=True)
class LandlordStatusView:
    profile_id: str
    user_id: str
    status: str
    can_publish: bool
    verified_at: str | None
    display_name: str | None
    latest_request: LandlordVerificationView | None = None

    @classmethod
    def from_aggregate(
        cls, profile: LandlordProfile, request: VerificationRequest | None
    ) -> LandlordStatusView:
        return cls(
            profile_id=str(profile.profile_id),
            user_id=str(profile.user_id),
            status=profile.verification_status.value,
            can_publish=profile.can_publish_property,
            verified_at=profile.verified_at.isoformat() if profile.verified_at else None,
            display_name=profile.display_name,
            latest_request=(
                LandlordVerificationView.from_aggregates(request, profile)
                if request is not None
                else None
            ),
        )

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)