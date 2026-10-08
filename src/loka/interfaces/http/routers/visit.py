"""Visit endpoints."""

from __future__ import annotations

import uuid
from datetime import date, datetime
from typing import Annotated

from fastapi import APIRouter, Depends, Request, status
from pydantic import BaseModel, Field

from loka.bounded_contexts.visit.application.dto.visit_dto import VisitView
from loka.bounded_contexts.visit.application.use_cases.cancel_visit import (
    CancelVisitCommand,
)
from loka.bounded_contexts.visit.application.use_cases.complete_visit import (
    CompleteVisitCommand,
)
from loka.bounded_contexts.visit.application.use_cases.get_visit import GetVisitQuery
from loka.bounded_contexts.visit.application.use_cases.request_visit import (
    RequestVisitCommand,
)
from loka.bounded_contexts.visit.application.use_cases.schedule_visit import (
    ScheduleVisitCommand,
)
from loka.interfaces.http.auth import Principal, require_principal
from loka.interfaces.http.container import get_container
from loka.shared.domain.clock import SystemClock
from loka.shared.infrastructure.composition import (
    cancel_visit_use_case,
    complete_visit_use_case,
    get_visit_use_case,
    request_visit_use_case,
    schedule_visit_use_case,
    unit_of_work,
)

router = APIRouter(prefix="/visits", tags=["visits"])
clock = SystemClock()


class RequestVisitRequest(BaseModel):
    property_id: uuid.UUID
    preferred_date: date | None = None
    message: str | None = Field(default=None, max_length=2000)


class ScheduleVisitRequest(BaseModel):
    scheduled_for: datetime


class CancelVisitRequest(BaseModel):
    reason: str | None = Field(default=None, max_length=2000)


class CompleteVisitRequest(BaseModel):
    attended: bool = True


@router.post("", status_code=status.HTTP_201_CREATED)
async def request_visit(
    body: RequestVisitRequest,
    request: Request,
    principal: Annotated[Principal, Depends(require_principal)],
) -> dict[str, object]:
    database = get_container(request).database
    async with unit_of_work(database) as uow:
        view: VisitView = await request_visit_use_case(uow).execute(
            RequestVisitCommand(
                property_id=body.property_id,
                tenant_id=principal.user_id,
                preferred_date=body.preferred_date,
                message=body.message,
            ),
            now=clock.now(),
        )
    return view.to_dict()


@router.get("/{visit_id}", status_code=status.HTTP_200_OK)
async def get_visit(
    visit_id: uuid.UUID,
    request: Request,
    principal: Annotated[Principal, Depends(require_principal)],
) -> dict[str, object]:
    database = get_container(request).database
    async with unit_of_work(database) as uow:
        view = await get_visit_use_case(uow).execute(
            GetVisitQuery(visit_id=visit_id, requester_id=principal.user_id)
        )
    return view.to_dict()


@router.post("/{visit_id}/schedule", status_code=status.HTTP_200_OK)
async def schedule_visit(
    visit_id: uuid.UUID,
    body: ScheduleVisitRequest,
    request: Request,
    principal: Annotated[Principal, Depends(require_principal)],
) -> dict[str, object]:
    database = get_container(request).database
    async with unit_of_work(database) as uow:
        view = await schedule_visit_use_case(uow).execute(
            ScheduleVisitCommand(
                visit_id=visit_id,
                actor_id=principal.user_id,
                scheduled_for=body.scheduled_for,
            ),
            now=clock.now(),
        )
    return view.to_dict()


@router.post("/{visit_id}/cancel", status_code=status.HTTP_200_OK)
async def cancel_visit(
    visit_id: uuid.UUID,
    body: CancelVisitRequest,
    request: Request,
    principal: Annotated[Principal, Depends(require_principal)],
) -> dict[str, object]:
    database = get_container(request).database
    async with unit_of_work(database) as uow:
        view = await cancel_visit_use_case(uow).execute(
            CancelVisitCommand(
                visit_id=visit_id, actor_id=principal.user_id, reason=body.reason
            ),
            now=clock.now(),
        )
    return view.to_dict()


@router.post("/{visit_id}/complete", status_code=status.HTTP_200_OK)
async def complete_visit(
    visit_id: uuid.UUID,
    body: CompleteVisitRequest,
    request: Request,
    principal: Annotated[Principal, Depends(require_principal)],
) -> dict[str, object]:
    database = get_container(request).database
    async with unit_of_work(database) as uow:
        view = await complete_visit_use_case(uow).execute(
            CompleteVisitCommand(
                visit_id=visit_id,
                actor_id=principal.user_id,
                attended=body.attended,
            ),
            now=clock.now(),
        )
    return view.to_dict()