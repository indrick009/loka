"""Landlord verification domain rules.

The aggregate is where the workflow invariants live: evidence before review,
one decision per request, and an entitlement that only flips on a real
decision. These tests pin those rules down without any infrastructure.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime

import pytest

from loka.bounded_contexts.landlord.domain.entities.landlord_profile import LandlordProfile
from loka.bounded_contexts.landlord.domain.entities.verification_request import (
    DocumentKind,
    IdentityDocument,
    RejectionReason,
    ReviewMode,
    VerificationRequest,
    VerificationStatus,
)
from loka.shared.domain.errors import InvalidStateTransition, InvariantViolation

NOW = datetime(2026, 3, 1, 9, 0, tzinfo=UTC)
USER_ID = uuid.uuid4()
LANDLORD_ID = uuid.uuid4()


def _request() -> VerificationRequest:
    return VerificationRequest(
        request_id=uuid.uuid4(), landlord_id=LANDLORD_ID, user_id=USER_ID, now=NOW
    )


def _document() -> IdentityDocument:
    return IdentityDocument(
        document_id=uuid.uuid4(),
        kind=DocumentKind.NATIONAL_ID_CARD,
        object_key="identity/cni-front.jpg.enc",
        checksum="a" * 64,
        captured_at=NOW,
    )


def _complete(request: VerificationRequest) -> VerificationRequest:
    request.add_document(_document(), now=NOW)
    request.attach_selfie(
        object_key="identity/selfie.jpg.enc", checksum="b" * 64, now=NOW
    )
    return request


def _submitted(*, risk_score: int | None = 10) -> VerificationRequest:
    request = _complete(_request())
    request.submit(now=NOW, risk_score=risk_score)
    return request


def test_a_fresh_request_starts_pending_and_cannot_be_submitted_empty() -> None:
    request = _request()
    assert request.status is VerificationStatus.PENDING
    assert sorted(request.missing_evidence()) == ["identity_document", "selfie"]

    with pytest.raises(InvariantViolation):
        request.submit(now=NOW)


def test_submitting_complete_evidence_moves_the_request_to_review() -> None:
    request = _submitted()

    assert request.status is VerificationStatus.UNDER_REVIEW
    assert request.review_mode is ReviewMode.AUTOMATIC
    assert request.submitted_at == NOW
    assert [event.event_type for event in request.pull_events()] == [
        "LandlordVerificationSubmitted"
    ]


def test_a_high_risk_score_routes_the_request_to_a_human() -> None:
    request = _submitted(risk_score=75)

    assert request.review_mode is ReviewMode.MANUAL


def test_evidence_cannot_be_attached_while_under_review() -> None:
    request = _submitted()

    with pytest.raises(InvalidStateTransition):
        request.add_document(_document(), now=NOW)


def test_approving_emits_the_verified_event_once() -> None:
    request = _submitted()
    request.pull_events()

    request.approve(now=NOW, reviewer_id=uuid.uuid4())
    assert request.status is VerificationStatus.VERIFIED
    assert request.is_verified is True

    events = request.pull_events()
    assert [event.event_type for event in events] == ["LandlordVerified"]
    assert events[0].metadata["landlord_id"] == str(LANDLORD_ID)


def test_a_pending_request_cannot_be_approved() -> None:
    with pytest.raises(InvalidStateTransition):
        _complete(_request()).approve(now=NOW)


def test_rejecting_keeps_the_reason_and_allows_a_new_attempt() -> None:
    request = _submitted()
    request.reject(reason=RejectionReason.BLURRED_OR_UNREADABLE, now=NOW)

    assert request.status is VerificationStatus.REJECTED
    assert request.rejection_reason is RejectionReason.BLURRED_OR_UNREADABLE

    # A rejected request accepts fresh evidence and returns to review.
    request.add_document(_document(), now=NOW)
    assert request.status is VerificationStatus.PENDING


def test_a_selfie_must_live_under_the_identity_prefix() -> None:
    request = _request()
    with pytest.raises(InvariantViolation):
        request.attach_selfie(object_key="wrong/selfie.jpg", checksum="b" * 64, now=NOW)


def test_profile_becomes_publishable_only_once_verified() -> None:
    profile = LandlordProfile(
        profile_id=uuid.uuid4(), user_id=USER_ID, display_name="Awa N.", now=NOW
    )
    assert profile.can_publish_property is False

    profile.mark_verified(now=NOW)
    assert profile.verification_status is VerificationStatus.VERIFIED
    assert profile.verified_at == NOW
    assert profile.can_publish_property is True


def test_suspending_a_profile_records_why() -> None:
    profile = LandlordProfile(
        profile_id=uuid.uuid4(), user_id=USER_ID, display_name=None, now=NOW
    )
    profile.suspend(reason="fraud", now=NOW)

    assert profile.verification_status is VerificationStatus.SUSPENDED
    assert profile.suspended_at == NOW
    events = profile.pull_events()
    assert [event.event_type for event in events] == ["LandlordSuspended"]