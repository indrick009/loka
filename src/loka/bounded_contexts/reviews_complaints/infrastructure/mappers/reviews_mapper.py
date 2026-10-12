"""Aggregate <-> row mappers.

Kept separate from the aggregates so the domain never sees an ORM object and
the aggregates never see SQL.
"""

from __future__ import annotations

from typing import Any

from loka.bounded_contexts.reviews_complaints.domain.entities.complaint import (
    Complaint,
    ComplaintReason,
    ComplaintStatus,
)
from loka.bounded_contexts.reviews_complaints.domain.entities.provider_standing import (
    ProviderStanding,
    StandingStatus,
)
from loka.bounded_contexts.reviews_complaints.domain.entities.review import Review
from loka.bounded_contexts.reviews_complaints.domain.value_objects.provider import (
    IntroductionRef,
    OfferRef,
    ProviderKind,
    ProviderRef,
    ServiceDomain,
)
from loka.bounded_contexts.reviews_complaints.infrastructure.persistence.models import (
    ProviderComplaintRow,
    ProviderStandingRow,
)


def review_to_row(review: Review) -> dict[str, Any]:
    return {
        "id": review.id,
        "domain": review.offer.domain.value,
        "provider_kind": review.provider.kind.value,
        "provider_id": review.provider.provider_id,
        "offer_id": review.offer.offer_id,
        "introduction_id": review.introduction.introduction_id,
        "author_id": review.author_id,
        "rating": review.rating.value,
        "submitted_at": review.submitted_at,
    }


def complaint_to_row(complaint: Complaint) -> dict[str, Any]:
    return {
        "id": complaint.id,
        "domain": complaint.offer.domain.value,
        "provider_kind": complaint.provider.kind.value,
        "provider_id": complaint.provider.provider_id,
        "offer_id": complaint.offer.offer_id,
        "introduction_id": complaint.introduction.introduction_id,
        "complainant_id": complaint.complainant_id,
        "reason": complaint.reason.value,
        "account": complaint.account,
        "status": complaint.status.value,
        "reviewer_id": complaint.reviewer_id,
        "resolution": complaint.resolution,
        "version": complaint.version,
        "filed_at": complaint.filed_at,
        "decided_at": complaint.decided_at,
        "updated_at": complaint.updated_at,
    }


def complaint_from_row(row: ProviderComplaintRow) -> Complaint:
    domain = ServiceDomain(row.domain)
    complaint = Complaint(
        complaint_id=row.id,
        provider=ProviderRef(
            kind=ProviderKind(row.provider_kind), provider_id=row.provider_id
        ),
        offer=OfferRef(domain=domain, offer_id=row.offer_id),
        introduction=IntroductionRef(domain=domain, introduction_id=row.introduction_id),
        complainant_id=row.complainant_id,
        reason=ComplaintReason(row.reason),
        account=row.account,
        now=row.filed_at,
    )
    complaint._assign_id(row.id)
    complaint.status = ComplaintStatus(row.status)
    complaint.reviewer_id = row.reviewer_id
    complaint.resolution = row.resolution
    complaint.decided_at = row.decided_at
    complaint.updated_at = row.updated_at
    complaint.pull_events()
    complaint.mark_persisted(row.version)
    return complaint


def standing_to_row(standing: ProviderStanding) -> dict[str, Any]:
    return {
        "id": standing.id,
        "provider_kind": standing.provider.kind.value,
        "provider_id": standing.provider.provider_id,
        "status": standing.status.value,
        "pending_complaints": standing.pending_complaints,
        "upheld_complaints": standing.upheld_complaints,
        "decided_by": standing.decided_by,
        "decision_note": standing.decision_note,
        "version": standing.version,
        "hidden_at": standing.hidden_at,
        "decided_at": standing.decided_at,
        "created_at": standing.created_at,
        "updated_at": standing.updated_at,
    }


def standing_from_row(row: ProviderStandingRow) -> ProviderStanding:
    standing = ProviderStanding(
        standing_id=row.id,
        provider=ProviderRef(
            kind=ProviderKind(row.provider_kind), provider_id=row.provider_id
        ),
        now=row.created_at,
    )
    standing._assign_id(row.id)
    standing.status = StandingStatus(row.status)
    standing.pending_complaints = row.pending_complaints
    standing.upheld_complaints = row.upheld_complaints
    standing.decided_by = row.decided_by
    standing.decision_note = row.decision_note
    standing.hidden_at = row.hidden_at
    standing.decided_at = row.decided_at
    standing.updated_at = row.updated_at
    standing.mark_persisted(row.version)
    return standing
