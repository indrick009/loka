"""Landlord verification endpoints.

The landlord drives the flow from WhatsApp in production, but the same use
cases are exposed over HTTP so the conversation layer and operators share one
implementation. Review is admin-only: approving an identity is a trust
decision, not a self-serve action.
"""

from __future__ import annotations

import uuid
from typing import Annotated

from fastapi import APIRouter, Depends, Request, status
from pydantic import BaseModel, Field

from loka.bounded_contexts.landlord.application.dto.landlord_verification_dto import (
    LandlordStatusView,
    LandlordVerificationView,
)
from loka.bounded_contexts.landlord.application.use_cases.get_landlord_verification_status import (
    LandlordStatusQuery,
)
from loka.bounded_contexts.landlord.application.use_cases.request_landlord_status import (
    RequestLandlordStatusCommand,
)
from loka.bounded_contexts.landlord.application.use_cases.review_landlord_verification import (
    ReviewDecision,
    ReviewLandlordVerificationCommand,
)
from loka.bounded_contexts.landlord.application.use_cases.submit_landlord_verification import (
    SubmitLandlordVerificationCommand,
)
from loka.bounded_contexts.landlord.domain.entities.verification_request import (
    DocumentKind,
    RejectionReason,
)
from loka.interfaces.http.auth import Principal, require_admin, require_principal
from loka.interfaces.http.container import get_container
from loka.shared.domain.clock import SystemClock
from loka.shared.infrastructure.composition import (
    get_landlord_verification_status_use_case,
    request_landlord_status_use_case,
    review_landlord_verification_use_case,
    submit_landlord_verification_use_case,
    unit_of_work,
)

router = APIRouter(prefix="/landlords", tags=["landlords"])
clock = SystemClock()


class RequestLandlordStatusRequest(BaseModel):
    display_name: str | None = Field(default=None, max_length=80)


class SubmitVerificationRequest(BaseModel):
    document_kind: DocumentKind
    document_object_key: str = Field(min_length=1, max_length=255)
    document_checksum: str = Field(min_length=1, max_length=64)
    selfie_object_key: str = Field(min_length=1, max_length=255)
    selfie_checksum: str = Field(min_length=1, max_length=64)
    risk_score: int | None = Field(default=None, ge=0, le=100)


class ReviewVerificationRequest(BaseModel):
    decision: ReviewDecision
    reason: RejectionReason | None = None
    notes: str | None = Field(default=None, max_length=2000)


@router.post("/verification/request", status_code=status.HTTP_200_OK)
async def request_landlord_status(
    body: RequestLandlordStatusRequest,
    request: Request,
    principal: Annotated[Principal, Depends(require_principal)],
) -> dict[str, object]:
    database = get_container(request).database
    async with unit_of_work(database) as uow:
        view: LandlordStatusView = await request_landlord_status_use_case(uow).execute(
            RequestLandlordStatusCommand(
                user_id=principal.user_id, display_name=body.display_name
            ),
            now=clock.now(),
        )
    return view.to_dict()


@router.get("/verification", status_code=status.HTTP_200_OK)
async def get_landlord_verification(
    request: Request,
    principal: Annotated[Principal, Depends(require_principal)],
) -> dict[str, object]:
    database = get_container(request).database
    async with unit_of_work(database) as uow:
        view = await get_landlord_verification_status_use_case(uow).execute(
            LandlordStatusQuery(user_id=principal.user_id)
        )
    return view.to_dict()


@router.post("/verification/submit", status_code=status.HTTP_200_OK)
async def submit_landlord_verification(
    body: SubmitVerificationRequest,
    request: Request,
    principal: Annotated[Principal, Depends(require_principal)],
) -> dict[str, object]:
    database = get_container(request).database
    async with unit_of_work(database) as uow:
        view: LandlordVerificationView = await submit_landlord_verification_use_case(
            uow
        ).execute(
            SubmitLandlordVerificationCommand(
                user_id=principal.user_id,
                document_kind=body.document_kind,
                document_object_key=body.document_object_key,
                document_checksum=body.document_checksum,
                selfie_object_key=body.selfie_object_key,
                selfie_checksum=body.selfie_checksum,
                risk_score=body.risk_score,
            ),
            now=clock.now(),
        )
    return view.to_dict()


@router.post("/verification/{request_id}/review", status_code=status.HTTP_200_OK)
async def review_landlord_verification(
    request_id: uuid.UUID,
    body: ReviewVerificationRequest,
    request: Request,
    principal: Annotated[Principal, Depends(require_admin)],
) -> dict[str, object]:
    database = get_container(request).database
    async with unit_of_work(database) as uow:
        view = await review_landlord_verification_use_case(uow).execute(
            ReviewLandlordVerificationCommand(
                request_id=request_id,
                decision=body.decision,
                reviewer_id=principal.user_id,
                reason=body.reason,
                notes=body.notes,
            ),
            now=clock.now(),
        )
    return view.to_dict()