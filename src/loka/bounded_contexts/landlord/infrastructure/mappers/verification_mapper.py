"""Landlord verification <-> row mapper.

Kept separate from the aggregates so the domain never sees an ORM object and
the aggregates never see SQL.
"""

from __future__ import annotations

import uuid
from typing import Any

from loka.bounded_contexts.landlord.domain.entities.landlord_profile import LandlordProfile
from loka.bounded_contexts.landlord.domain.entities.verification_request import (
    DocumentKind,
    IdentityDocument,
    RejectionReason,
    ReviewMode,
    VerificationRequest,
    VerificationStatus,
)
from loka.bounded_contexts.landlord.infrastructure.persistence.models import (
    LandlordProfileRow,
    VerificationDocumentRow,
    VerificationRequestRow,
)


def profile_to_row(profile: LandlordProfile) -> dict[str, Any]:
    return {
        "id": profile.profile_id,
        "user_id": profile.user_id,
        "display_name": profile.display_name,
        "verification_status": profile.verification_status.value,
        "verified_at": profile.verified_at,
        "trust_score": profile.trust_score,
        "successful_rentals": profile.successful_rentals,
        "active_property_count": profile.active_property_count,
        "suspended_at": profile.suspended_at,
        "revision": profile.version,
        "created_at": profile.created_at,
        "updated_at": profile.updated_at,
    }


def profile_from_row(row: LandlordProfileRow) -> LandlordProfile:
    profile = LandlordProfile(
        profile_id=row.id,
        user_id=row.user_id,
        display_name=row.display_name,
        now=row.created_at,
    )
    profile.verification_status = VerificationStatus(row.verification_status)
    profile.verified_at = row.verified_at
    profile.trust_score = row.trust_score
    profile.successful_rentals = row.successful_rentals
    profile.active_property_count = row.active_property_count
    profile.suspended_at = row.suspended_at
    profile.created_at = row.created_at
    profile.updated_at = row.updated_at
    profile.mark_persisted(row.revision)
    return profile


def request_to_row(request: VerificationRequest) -> dict[str, Any]:
    return {
        "id": request.request_id,
        "landlord_id": request.landlord_id,
        "user_id": request.user_id,
        "status": request.status.value,
        "review_mode": request.review_mode.value,
        "rejection_reason": (
            request.rejection_reason.value if request.rejection_reason else None
        ),
        "risk_score": request.risk_score,
        "selfie_object_key": request.selfie_object_key,
        "selfie_checksum": request.selfie_checksum,
        "decided_at": request.decided_at,
        "decided_by": request.decided_by,
        "review_notes": request.review_notes,
        "submitted_at": request.submitted_at,
        "revision": request.version,
        "created_at": request.created_at,
        "updated_at": request.updated_at,
    }


def document_to_row(document: IdentityDocument, request_id: uuid.UUID) -> dict[str, Any]:
    return {
        "id": document.document_id,
        "request_id": request_id,
        "kind": document.kind.value,
        "object_key": document.object_key,
        "checksum": document.checksum,
        "encrypted": document.encrypted,
        "captured_at": document.captured_at,
        "created_at": document.captured_at,
    }


def request_from_row(
    row: VerificationRequestRow,
    document_rows: list[VerificationDocumentRow] | None = None,
) -> VerificationRequest:
    request = VerificationRequest(
        request_id=row.id,
        landlord_id=row.landlord_id,
        user_id=row.user_id,
        now=row.created_at,
    )
    request.status = VerificationStatus(row.status)
    request.review_mode = ReviewMode(row.review_mode)
    request.rejection_reason = (
        RejectionReason(row.rejection_reason) if row.rejection_reason else None
    )
    request.risk_score = row.risk_score
    request.selfie_object_key = row.selfie_object_key
    request.selfie_checksum = row.selfie_checksum
    request.decided_at = row.decided_at
    request.decided_by = row.decided_by
    request.review_notes = row.review_notes
    request.submitted_at = row.submitted_at
    request.revision = row.revision
    request.created_at = row.created_at
    request.updated_at = row.updated_at
    request.documents = [
        IdentityDocument(
            document_id=document.id,
            kind=DocumentKind(document.kind),
            object_key=document.object_key,
            checksum=document.checksum,
            captured_at=document.captured_at,
            encrypted=document.encrypted,
        )
        for document in (document_rows or [])
    ]
    request.mark_persisted(row.revision)
    return request


# Alias kept for symmetry with the other mappers in the codebase.
from_row = request_from_row