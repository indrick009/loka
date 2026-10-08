"""Let a model pre-decide a landlord verification.

Runs after ``LandlordVerificationSubmitted``. The model is shown the two
evidence images and returns a verdict; the domain policy decides whether that
verdict is enough to approve on its own. Anything short of a confident, low-risk
approval stays ``UNDER_REVIEW`` for an operator — the queue is the fallback, so a
disabled model, an outage or a doubtful answer never blocks a real person.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import datetime
from typing import Any

from loka.bounded_contexts.landlord.application.ports import (
    EvidenceUrlProvider,
    VerificationAdjudicator,
    VerificationEvidence,
)
from loka.bounded_contexts.landlord.domain.entities.landlord_profile import LandlordProfile
from loka.bounded_contexts.landlord.domain.entities.verification_request import (
    VerificationRequest,
    VerificationStatus,
)
from loka.bounded_contexts.landlord.domain.repositories.verification_repository import (
    LANDLORD_PROFILE_REPOSITORY,
    VERIFICATION_REQUEST_REPOSITORY,
)
from loka.bounded_contexts.landlord.domain.services.verification_adjudication import (
    ReviewRoute,
    VerificationAdjudicationPolicy,
)
from loka.shared.application.unit_of_work import UnitOfWork
from loka.shared.application.use_case import UseCase
from loka.shared.domain.errors import ExternalServiceUnavailable, RateLimited, ResourceNotFound
from loka.shared.infrastructure.logging import get_logger

log = get_logger(__name__)


@dataclass(frozen=True, slots=True)
class AdjudicateLandlordVerificationCommand:
    request_id: uuid.UUID


@dataclass(frozen=True, slots=True)
class AdjudicationView:
    request_id: str
    status: str
    route: str
    decision: str | None
    confidence: float | None
    reason: str | None
    can_publish: bool

    def to_dict(self) -> dict[str, Any]:
        return {
            "request_id": self.request_id,
            "status": self.status,
            "route": self.route,
            "decision": self.decision,
            "confidence": self.confidence,
            "reason": self.reason,
            "can_publish": self.can_publish,
        }


class AdjudicateLandlordVerificationUseCase(
    UseCase[AdjudicateLandlordVerificationCommand, AdjudicationView]
):
    name = "landlord.adjudicate_verification"

    def __init__(
        self,
        uow: UnitOfWork,
        *,
        adjudicator: VerificationAdjudicator | None,
        urls: EvidenceUrlProvider,
        policy: VerificationAdjudicationPolicy | None = None,
    ) -> None:
        super().__init__(uow)
        self._profiles = uow.repository(LANDLORD_PROFILE_REPOSITORY)
        self._requests = uow.repository(VERIFICATION_REQUEST_REPOSITORY)
        self._adjudicator = adjudicator
        self._urls = urls
        self._policy = policy or VerificationAdjudicationPolicy()

    async def execute(  # type: ignore[override]
        self, command: AdjudicateLandlordVerificationCommand, *, now: datetime
    ) -> AdjudicationView:
        request = await self._requests.get(command.request_id)
        if request is None:
            raise ResourceNotFound(
                "verification request not found",
                context={"request_id": str(command.request_id)},
            )
        profile = await self._profiles.get(request.landlord_id)
        if profile is None:
            raise ResourceNotFound(
                "landlord profile not found for this request",
                context={"landlord_id": str(request.landlord_id)},
            )

        if request.status is not VerificationStatus.UNDER_REVIEW or not request.documents:
            return self._view(request, profile, route=ReviewRoute.MANUAL, reason=None)

        if self._adjudicator is None:
            self._log("landlord_adjudication_skipped", request_id=str(request.request_id))
            return self._view(request, profile, route=ReviewRoute.MANUAL, reason=None)

        document = request.documents[-1]
        evidence = VerificationEvidence(
            request_id=request.request_id,
            landlord_id=request.landlord_id,
            document_kind=document.kind,
            document_url=await self._urls.url_for(document.object_key),
            selfie_url=await self._urls.url_for(request.selfie_object_key or ""),
            risk_score=request.risk_score,
        )

        try:
            adjudication = await self._adjudicator.adjudicate(evidence)
        except (ExternalServiceUnavailable, RateLimited) as exc:
            # The model being down must not decide anything. The request keeps
            # its UNDER_REVIEW state and the operator queue absorbs it.
            self._log(
                "landlord_adjudication_unavailable",
                request_id=str(request.request_id),
                error=str(exc),
            )
            return self._view(request, profile, route=ReviewRoute.MANUAL, reason=None)

        route = self._policy.route(adjudication, risk_score=request.risk_score)
        if route is ReviewRoute.AUTO_APPROVE:
            request.approve(now=now, notes=f"auto-approved by model: {adjudication.reason}")
            profile.mark_verified(now=now)
            await self._requests.save(request)
            await self._profiles.save(profile)
            self._uow.collect(request)
            self._uow.collect(profile)
            await self._uow.commit()

        self._log(
            "landlord_verification_adjudicated",
            request_id=str(request.request_id),
            route=route.value,
            decision=adjudication.decision.value,
            confidence=adjudication.confidence,
        )
        return self._view(
            request,
            profile,
            route=route,
            reason=adjudication.reason,
            decision=adjudication.decision.value,
            confidence=adjudication.confidence,
        )

    def _view(
        self,
        request: VerificationRequest,
        profile: LandlordProfile,
        *,
        route: ReviewRoute,
        reason: str | None,
        decision: str | None = None,
        confidence: float | None = None,
    ) -> AdjudicationView:
        return AdjudicationView(
            request_id=str(request.request_id),
            status=request.status.value,
            route=route.value,
            decision=decision,
            confidence=confidence,
            reason=reason,
            can_publish=profile.can_publish_property,
        )


__all__ = [
    "AdjudicateLandlordVerificationCommand",
    "AdjudicateLandlordVerificationUseCase",
    "AdjudicationView",
]