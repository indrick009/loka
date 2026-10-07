"""Landlord profile aggregate.

The profile is the durable answer to "may this account list a property?" and
the read model publication gates on. It is deliberately separate from the
:class:`VerificationRequest`, which owns the evidence and the decision trail:
the profile carries the current entitlement, the request carries the history.
"""

from __future__ import annotations

import uuid
from datetime import datetime

from loka.bounded_contexts.landlord.domain.entities.verification_request import (
    VerificationStatus,
)
from loka.shared.domain.aggregate import AggregateRoot
from loka.shared.domain.clock import ensure_utc


class LandlordProfile(AggregateRoot):
    __slots__ = (
        "active_property_count",
        "created_at",
        "display_name",
        "profile_id",
        "successful_rentals",
        "suspended_at",
        "trust_score",
        "updated_at",
        "user_id",
        "verification_status",
        "verified_at",
    )

    def __init__(
        self,
        *,
        profile_id: uuid.UUID,
        user_id: uuid.UUID,
        display_name: str | None,
        now: datetime,
    ) -> None:
        super().__init__(aggregate_type="LandlordProfile")
        self._assign_id(profile_id)
        self.profile_id = profile_id
        self.user_id = user_id
        self.display_name = (display_name or "").strip() or None
        self.verification_status = VerificationStatus.PENDING
        self.verified_at: datetime | None = None
        self.trust_score = 0
        self.successful_rentals = 0
        self.active_property_count = 0
        self.suspended_at: datetime | None = None
        self.created_at = ensure_utc(now)
        self.updated_at = ensure_utc(now)

    @property
    def is_verified(self) -> bool:
        return self.verification_status is VerificationStatus.VERIFIED

    @property
    def can_publish_property(self) -> bool:
        return self.is_verified

    def mark_verified(self, *, now: datetime) -> None:
        """Mirror an approved verification. The event belongs to the request."""
        if self.verification_status is VerificationStatus.VERIFIED:
            return
        self.verification_status = VerificationStatus.VERIFIED
        self.verified_at = ensure_utc(now)
        self.suspended_at = None
        self._touch(now)

    def mark_rejected(self, *, now: datetime) -> None:
        if self.verification_status is VerificationStatus.VERIFIED:
            return
        self.verification_status = VerificationStatus.REJECTED
        self._touch(now)

    def suspend(self, *, reason: str, now: datetime, actor_id: uuid.UUID | None = None) -> None:
        if self.verification_status is VerificationStatus.SUSPENDED:
            return
        self.verification_status = VerificationStatus.SUSPENDED
        self.suspended_at = ensure_utc(now)
        self._touch(now)
        self.record(
            "LandlordSuspended",
            occurred_at=now,
            actor_id=actor_id,
            payload={"profile_id": str(self.profile_id), "reason": reason},
        )

    def reinstate(self, *, now: datetime) -> None:
        if self.verification_status is not VerificationStatus.SUSPENDED:
            return
        self.verification_status = (
            VerificationStatus.VERIFIED
            if self.verified_at is not None
            else VerificationStatus.PENDING
        )
        self.suspended_at = None
        self._touch(now)

    def rename(self, display_name: str, *, now: datetime) -> None:
        cleaned = display_name.strip()
        if not 2 <= len(cleaned) <= 80:
            self._reject("display name must be 2 to 80 characters", length=len(cleaned))
        self.display_name = cleaned
        self._touch(now)

    def _touch(self, now: datetime) -> None:
        self.updated_at = ensure_utc(now)
        self._bump()