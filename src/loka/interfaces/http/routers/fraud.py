"""Analyst fraud endpoints.

Every route here is guarded by ``require_admin``: risk profiles are a tool for
the trust & safety team, and the score is a recommendation, never a self-serve
decision surface for tenants or landlords.
"""

from __future__ import annotations

import uuid
from typing import Annotated, Literal

from fastapi import APIRouter, Depends, Request, status
from pydantic import BaseModel

from loka.bounded_contexts.fraud.application.use_cases.evaluate_risk import (
    EvaluateRiskCommand,
)
from loka.bounded_contexts.fraud.application.use_cases.get_escalation_queue import (
    EscalationQueueQuery,
)
from loka.bounded_contexts.fraud.application.use_cases.get_fraud_summary import (
    FraudSummaryQuery,
)
from loka.bounded_contexts.fraud.application.use_cases.get_risk_profile import (
    RiskProfileQuery,
)
from loka.bounded_contexts.fraud.application.use_cases.review_risk import (
    ReviewRiskCommand,
    ReviewRiskDecision,
)
from loka.bounded_contexts.fraud.domain.entities.risk_profile import RiskSubject
from loka.interfaces.http.auth import Principal, require_admin
from loka.interfaces.http.container import get_container
from loka.shared.domain.clock import SystemClock
from loka.shared.infrastructure.composition import (
    evaluate_risk_use_case,
    get_escalation_queue_use_case,
    get_fraud_summary_use_case,
    get_risk_profile_use_case,
    review_risk_use_case,
    unit_of_work,
)

router = APIRouter(prefix="/fraud", tags=["fraud"])
clock = SystemClock()

_SUBJECTS: dict[str, RiskSubject] = {
    "LANDLORD": RiskSubject.LANDLORD,
    "USER": RiskSubject.USER,
    "PROPERTY": RiskSubject.PROPERTY,
}


class ReviewRiskRequest(BaseModel):
    decision: ReviewRiskDecision
    note: str | None = None


@router.get("/risk/{subject}/{subject_id}", status_code=status.HTTP_200_OK)
async def get_risk_profile(
    subject: Literal["LANDLORD", "USER", "PROPERTY"],
    subject_id: uuid.UUID,
    request: Request,
    _: Annotated[Principal, Depends(require_admin)],
) -> dict[str, object]:
    database = get_container(request).database
    async with unit_of_work(database) as uow:
        view = await get_risk_profile_use_case(uow).execute(
            RiskProfileQuery(subject=_SUBJECTS[subject], subject_id=subject_id),
            now=clock.now(),
        )
    return view.to_dict()


@router.post("/risk/{subject}/{subject_id}/evaluate", status_code=status.HTTP_200_OK)
async def evaluate_risk(
    subject: Literal["LANDLORD", "USER", "PROPERTY"],
    subject_id: uuid.UUID,
    request: Request,
    _: Annotated[Principal, Depends(require_admin)],
) -> dict[str, object]:
    database = get_container(request).database
    async with unit_of_work(database) as uow:
        view = await evaluate_risk_use_case(uow).execute(
            EvaluateRiskCommand(subject=_SUBJECTS[subject], subject_id=subject_id),
            now=clock.now(),
        )
    return view.to_dict()


@router.get("/escalations", status_code=status.HTTP_200_OK)
async def get_escalation_queue(
    request: Request,
    _: Annotated[Principal, Depends(require_admin)],
    limit: int = 50,
) -> dict[str, object]:
    database = get_container(request).database
    async with unit_of_work(database) as uow:
        view = await get_escalation_queue_use_case(uow).execute(
            EscalationQueueQuery(limit=limit),
            now=clock.now(),
        )
    return view.to_dict()


@router.get("/summary", status_code=status.HTTP_200_OK)
async def get_fraud_summary(
    request: Request,
    _: Annotated[Principal, Depends(require_admin)],
) -> dict[str, object]:
    database = get_container(request).database
    async with unit_of_work(database) as uow:
        view = await get_fraud_summary_use_case(uow).execute(
            FraudSummaryQuery(),
            now=clock.now(),
        )
    return view.to_dict()


@router.post("/risk/{profile_id}/review", status_code=status.HTTP_200_OK)
async def review_risk(
    profile_id: uuid.UUID,
    body: ReviewRiskRequest,
    request: Request,
    principal: Annotated[Principal, Depends(require_admin)],
) -> dict[str, object]:
    database = get_container(request).database
    async with unit_of_work(database) as uow:
        view = await review_risk_use_case(uow).execute(
            ReviewRiskCommand(
                profile_id=profile_id,
                decision=body.decision,
                actor_id=principal.user_id,
                note=body.note,
            ),
            now=clock.now(),
        )
    return view.to_dict()