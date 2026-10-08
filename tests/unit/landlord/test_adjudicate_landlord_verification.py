"""AdjudicateLandlordVerificationUseCase: the AI pre-decision and its fallback."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime
from typing import Any

import pytest
from landlord_fakes import (
    FakeLandlordProfileRepository,
    FakeUnitOfWork,
    FakeVerificationRequestRepository,
)

from loka.bounded_contexts.landlord.application.ports import VerificationEvidence
from loka.bounded_contexts.landlord.application.use_cases.adjudicate_landlord_verification import (
    AdjudicateLandlordVerificationCommand,
    AdjudicateLandlordVerificationUseCase,
)
from loka.bounded_contexts.landlord.domain.entities.landlord_profile import LandlordProfile
from loka.bounded_contexts.landlord.domain.entities.verification_request import (
    DocumentKind,
    IdentityDocument,
    VerificationRequest,
)
from loka.bounded_contexts.landlord.domain.services.verification_adjudication import (
    Adjudication,
    AdjudicationDecision,
    ReviewRoute,
)
from loka.shared.domain.errors import ExternalServiceUnavailable, ResourceNotFound

pytestmark = pytest.mark.unit

NOW = datetime(2026, 3, 1, 9, 0, tzinfo=UTC)
USER_ID = uuid.uuid4()


class FakeAdjudicator:
    def __init__(
        self,
        result: Adjudication | None = None,
        error: Exception | None = None,
    ) -> None:
        self.result = result
        self.error = error
        self.seen: VerificationEvidence | None = None

    async def adjudicate(self, evidence: VerificationEvidence) -> Adjudication:
        self.seen = evidence
        if self.error is not None:
            raise self.error
        assert self.result is not None
        return self.result


class FakeUrls:
    def __init__(self) -> None:
        self.requested: list[str] = []

    async def url_for(self, object_key: str) -> str:
        self.requested.append(object_key)
        return f"memory://{object_key}"


def _uow() -> FakeUnitOfWork:
    return FakeUnitOfWork(FakeLandlordProfileRepository(), FakeVerificationRequestRepository())


def _seed(uow: FakeUnitOfWork, *, risk_score: int = 10) -> VerificationRequest:
    profile = LandlordProfile(
        profile_id=uuid.uuid4(), user_id=USER_ID, display_name="Awa N.", now=NOW
    )
    uow.profiles.profiles[profile.profile_id] = profile

    request = VerificationRequest(
        request_id=uuid.uuid4(),
        landlord_id=profile.profile_id,
        user_id=USER_ID,
        now=NOW,
    )
    request.add_document(
        IdentityDocument(
            document_id=uuid.uuid4(),
            kind=DocumentKind.NATIONAL_ID_CARD,
            object_key="identity/cni.jpg",
            checksum="a" * 64,
            captured_at=NOW,
        ),
        now=NOW,
    )
    request.attach_selfie(object_key="identity/selfie.jpg", checksum="b" * 64, now=NOW)
    request.submit(now=NOW, risk_score=risk_score)
    uow.requests.requests[request.request_id] = request
    return request


def _use_case(
    uow: FakeUnitOfWork,
    *,
    adjudicator: Any,
    urls: FakeUrls | None = None,
) -> AdjudicateLandlordVerificationUseCase:
    return AdjudicateLandlordVerificationUseCase(
        uow, adjudicator=adjudicator, urls=urls or FakeUrls()
    )


def _approve(confidence: float) -> Adjudication:
    return Adjudication(
        decision=AdjudicationDecision.APPROVE, confidence=confidence, reason="clear"
    )


async def test_a_confident_low_risk_approval_is_applied_and_published() -> None:
    uow = _uow()
    request = _seed(uow)
    urls = FakeUrls()

    view = await _use_case(uow, adjudicator=FakeAdjudicator(_approve(0.97)), urls=urls).execute(
        AdjudicateLandlordVerificationCommand(request_id=request.request_id), now=NOW
    )

    assert view.route == ReviewRoute.AUTO_APPROVE.value
    assert view.status == "VERIFIED"
    assert view.can_publish is True
    assert uow.commits == 1
    assert request.status.value == "VERIFIED"
    assert urls.requested == ["identity/cni.jpg", "identity/selfie.jpg"]


async def test_the_evidence_never_carries_object_keys() -> None:
    uow = _uow()
    request = _seed(uow)
    adjudicator = FakeAdjudicator(_approve(0.95))

    await _use_case(uow, adjudicator=adjudicator).execute(
        AdjudicateLandlordVerificationCommand(request_id=request.request_id), now=NOW
    )

    assert adjudicator.seen is not None
    assert adjudicator.seen.document_url.startswith("memory://")
    assert adjudicator.seen.selfie_url.startswith("memory://")


async def test_a_lukewarm_approval_stays_for_an_operator() -> None:
    uow = _uow()
    request = _seed(uow)

    view = await _use_case(uow, adjudicator=FakeAdjudicator(_approve(0.8))).execute(
        AdjudicateLandlordVerificationCommand(request_id=request.request_id), now=NOW
    )

    assert view.route == ReviewRoute.MANUAL.value
    assert request.status.value == "UNDER_REVIEW"
    assert uow.commits == 0


async def test_a_high_risk_submission_is_never_auto_approved() -> None:
    uow = _uow()
    request = _seed(uow, risk_score=80)

    view = await _use_case(uow, adjudicator=FakeAdjudicator(_approve(0.99))).execute(
        AdjudicateLandlordVerificationCommand(request_id=request.request_id), now=NOW
    )

    assert view.route == ReviewRoute.MANUAL.value
    assert request.status.value == "UNDER_REVIEW"
    assert uow.commits == 0


async def test_a_model_rejection_goes_to_a_human() -> None:
    uow = _uow()
    request = _seed(uow)
    verdict = Adjudication(
        decision=AdjudicationDecision.REJECT, confidence=0.99, reason="mismatch"
    )

    view = await _use_case(uow, adjudicator=FakeAdjudicator(verdict)).execute(
        AdjudicateLandlordVerificationCommand(request_id=request.request_id), now=NOW
    )

    assert view.route == ReviewRoute.MANUAL.value
    assert view.decision == "REJECT"
    assert request.status.value == "UNDER_REVIEW"


async def test_a_model_outage_falls_back_to_the_operator_queue() -> None:
    uow = _uow()
    request = _seed(uow)

    view = await _use_case(
        uow, adjudicator=FakeAdjudicator(error=ExternalServiceUnavailable("down"))
    ).execute(
        AdjudicateLandlordVerificationCommand(request_id=request.request_id), now=NOW
    )

    assert view.route == ReviewRoute.MANUAL.value
    assert request.status.value == "UNDER_REVIEW"
    assert uow.commits == 0


async def test_a_disabled_pipeline_leaves_the_request_untouched() -> None:
    uow = _uow()
    request = _seed(uow)

    view = await _use_case(uow, adjudicator=None).execute(
        AdjudicateLandlordVerificationCommand(request_id=request.request_id), now=NOW
    )

    assert view.route == ReviewRoute.MANUAL.value
    assert request.status.value == "UNDER_REVIEW"
    assert uow.commits == 0


async def test_an_already_decided_request_is_not_re_judged() -> None:
    uow = _uow()
    request = _seed(uow)
    request.approve(now=NOW)
    adjudicator = FakeAdjudicator(_approve(0.99))

    view = await _use_case(uow, adjudicator=adjudicator).execute(
        AdjudicateLandlordVerificationCommand(request_id=request.request_id), now=NOW
    )

    assert view.route == ReviewRoute.MANUAL.value
    assert adjudicator.seen is None


async def test_an_unknown_request_is_not_found() -> None:
    with pytest.raises(ResourceNotFound):
        await _use_case(_uow(), adjudicator=FakeAdjudicator(_approve(0.99))).execute(
            AdjudicateLandlordVerificationCommand(request_id=uuid.uuid4()), now=NOW
        )