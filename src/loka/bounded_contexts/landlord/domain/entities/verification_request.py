"""Landlord verification aggregate.

Verification lives in its own bounded context: it decides whether an
identity is trustworthy, not whether a property is rentable. Publication is
gated by a read-only projection of this aggregate exposed through the
``LandlordDirectory`` port.
"""

from __future__ import annotations

import uuid
from datetime import datetime
from enum import StrEnum

from loka.shared.domain.aggregate import AggregateRoot
from loka.shared.domain.clock import ensure_utc
from loka.shared.domain.errors import InvalidStateTransition


class VerificationStatus(StrEnum):
    PENDING = "PENDING"
    UNDER_REVIEW = "UNDER_REVIEW"
    VERIFIED = "VERIFIED"
    REJECTED = "REJECTED"
    SUSPENDED = "SUSPENDED"


class DocumentKind(StrEnum):
    NATIONAL_ID_CARD = "NATIONAL_ID_CARD"
    PASSPORT = "PASSPORT"
    DRIVING_LICENCE = "DRIVING_LICENCE"
    RESIDENCE_PERMIT = "RESIDENCE_PERMIT"


class RejectionReason(StrEnum):
    BLURRED_OR_UNREADABLE = "BLURRED_OR_UNREADABLE"
    DOCUMENT_EXPIRED = "DOCUMENT_EXPIRED"
    DOCUMENT_MISMATCH = "DOCUMENT_MISMATCH"
    SELFIE_MISMATCH = "SELFIE_MISMATCH"
    DUPLICATE_IDENTITY = "DUPLICATE_IDENTITY"
    SUSPECTED_FRAUD = "SUSPECTED_FRAUD"
    INSUFFICIENT_EVIDENCE = "INSUFFICIENT_EVIDENCE"


class ReviewMode(StrEnum):
    AUTOMATIC = "AUTOMATIC"
    MANUAL = "MANUAL"


class IdentityDocument:
    """Reference to an encrypted object; the bytes never live in Postgres."""

    __slots__ = ("captured_at", "checksum", "document_id", "encrypted", "kind", "object_key")

    def __init__(
        self,
        *,
        document_id: uuid.UUID,
        kind: DocumentKind,
        object_key: str,
        checksum: str,
        captured_at: datetime,
        encrypted: bool = True,
    ) -> None:
        if not encrypted:
            raise ValueError("identity documents must be stored encrypted")
        if not object_key.startswith("identity/"):
            raise ValueError("identity documents must live under the identity/ prefix")
        self.document_id = document_id
        self.kind = kind
        self.object_key = object_key
        self.checksum = checksum
        self.captured_at = ensure_utc(captured_at)
        self.encrypted = encrypted


class VerificationRequest(AggregateRoot):
    __slots__ = (
        "created_at",
        "decided_at",
        "decided_by",
        "documents",
        "landlord_id",
        "rejection_reason",
        "request_id",
        "review_mode",
        "review_notes",
        "revision",
        "risk_score",
        "selfie_checksum",
        "selfie_object_key",
        "status",
        "submitted_at",
        "updated_at",
        "user_id",
    )

    def __init__(
        self,
        *,
        request_id: uuid.UUID,
        landlord_id: uuid.UUID,
        user_id: uuid.UUID,
        now: datetime,
    ) -> None:
        super().__init__(aggregate_type="VerificationRequest")
        self._assign_id(request_id)
        self.request_id = request_id
        self.landlord_id = landlord_id
        self.user_id = user_id
        self.status = VerificationStatus.PENDING
        self.documents: list[IdentityDocument] = []
        self.selfie_object_key: str | None = None
        self.selfie_checksum: str | None = None
        self.review_mode = ReviewMode.AUTOMATIC
        self.rejection_reason: RejectionReason | None = None
        self.risk_score: int | None = None
        self.decided_at: datetime | None = None
        self.decided_by: uuid.UUID | None = None
        self.submitted_at: datetime | None = None
        self.review_notes: str | None = None
        self.revision = 0
        self.created_at = ensure_utc(now)
        self.updated_at = ensure_utc(now)

    @property
    def is_verified(self) -> bool:
        return self.status is VerificationStatus.VERIFIED

    @property
    def can_publish_property(self) -> bool:
        """The single gate publication relies on."""
        return self.status is VerificationStatus.VERIFIED

    def add_document(self, document: IdentityDocument, *, now: datetime) -> None:
        if self.status not in (VerificationStatus.PENDING, VerificationStatus.REJECTED):
            raise InvalidStateTransition(
                f"cannot attach a document while {self.status.value}",
                context={"request_id": str(self.id)},
            )
        self.documents.append(document)
        self.status = VerificationStatus.PENDING
        self.rejection_reason = None
        self._touch(now)

    def attach_selfie(self, *, object_key: str, checksum: str, now: datetime) -> None:
        if not object_key.startswith("identity/"):
            self._reject("selfie must be stored under the identity/ prefix")
        self.selfie_object_key = object_key
        self.selfie_checksum = checksum
        self._touch(now)

    def submit(self, *, now: datetime, risk_score: int | None = None) -> None:
        if self.status is VerificationStatus.SUSPENDED:
            raise InvalidStateTransition("a suspended verification cannot be submitted")
        missing = self.missing_evidence()
        if missing:
            self._reject("verification evidence is incomplete", missing=missing)
        self.revision += 1
        self.status = VerificationStatus.UNDER_REVIEW
        self.risk_score = risk_score
        self.submitted_at = ensure_utc(now)
        self.review_mode = (
            ReviewMode.AUTOMATIC if (risk_score or 0) < 60 else ReviewMode.MANUAL
        )
        self._touch(now)
        self.record(
            "LandlordVerificationSubmitted",
            occurred_at=now,
            payload={
                "request_id": str(self.id),
                "landlord_id": str(self.landlord_id),
                "review_mode": self.review_mode.value,
                "risk_score": risk_score,
            },
        )

    def missing_evidence(self) -> list[str]:
        missing: list[str] = []
        if not self.documents:
            missing.append("identity_document")
        if not self.selfie_object_key:
            missing.append("selfie")
        return missing

    def approve(
        self,
        *,
        now: datetime,
        reviewer_id: uuid.UUID | None = None,
        notes: str | None = None,
    ) -> None:
        if self.status is not VerificationStatus.UNDER_REVIEW:
            raise InvalidStateTransition(
                f"cannot approve a {self.status.value} verification",
                context={"request_id": str(self.id)},
            )
        self.status = VerificationStatus.VERIFIED
        self.rejection_reason = None
        self.decided_at = ensure_utc(now)
        self.decided_by = reviewer_id
        self.review_notes = notes
        self._touch(now)
        self.record(
            "LandlordVerified",
            occurred_at=now,
            actor_id=reviewer_id,
            payload={
                "request_id": str(self.id),
                "landlord_id": str(self.landlord_id),
                "user_id": str(self.user_id),
                "review_mode": self.review_mode.value,
            },
        )

    def reject(
        self,
        *,
        reason: RejectionReason,
        now: datetime,
        reviewer_id: uuid.UUID | None = None,
        notes: str | None = None,
    ) -> None:
        if self.status is VerificationStatus.SUSPENDED:
            raise InvalidStateTransition("a suspended verification cannot be rejected")
        if self.status not in (
            VerificationStatus.PENDING,
            VerificationStatus.UNDER_REVIEW,
        ):
            raise InvalidStateTransition(
                f"cannot reject a {self.status.value} verification",
                context={"request_id": str(self.id)},
            )
        self.status = VerificationStatus.REJECTED
        self.rejection_reason = reason
        self.decided_at = ensure_utc(now)
        self.decided_by = reviewer_id
        self.review_notes = notes
        self._touch(now)
        self.record(
            "LandlordVerificationRejected",
            occurred_at=now,
            actor_id=reviewer_id,
            payload={
                "request_id": str(self.id),
                "landlord_id": str(self.landlord_id),
                "reason": reason.value,
            },
        )

    def suspend(self, *, reason: str, now: datetime, actor_id: uuid.UUID | None = None) -> None:
        if self.status is VerificationStatus.SUSPENDED:
            return
        self.status = VerificationStatus.SUSPENDED
        self.rejection_reason = RejectionReason.SUSPECTED_FRAUD
        self.review_notes = reason
        self._touch(now)
        self.record(
            "LandlordVerificationSuspended",
            occurred_at=now,
            actor_id=actor_id,
            payload={"request_id": str(self.id), "reason": reason},
        )

    def _touch(self, now: datetime) -> None:
        self.updated_at = ensure_utc(now)
        self._bump()