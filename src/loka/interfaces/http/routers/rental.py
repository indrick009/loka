"""Rental application endpoints.

The WhatsApp conversation layer drives the same use cases; these routes exist so
operators and tests share one implementation. The actor is always the
authenticated principal, never a body field.
"""

from __future__ import annotations

import uuid
from typing import Annotated

from fastapi import APIRouter, Depends, Request, status
from pydantic import BaseModel, Field

from loka.bounded_contexts.property.domain.value_objects.money import Money
from loka.bounded_contexts.rental.application.dto.rental_dto import RentalApplicationView
from loka.bounded_contexts.rental.application.use_cases.confirm_rental import (
    ConfirmRentalCommand,
)
from loka.bounded_contexts.rental.application.use_cases.decide_rental_application import (
    DecideRentalApplicationCommand,
    RentalDecision,
)
from loka.bounded_contexts.rental.application.use_cases.express_rental_interest import (
    ExpressRentalInterestCommand,
)
from loka.bounded_contexts.rental.application.use_cases.get_rental_application import (
    GetRentalApplicationQuery,
)
from loka.bounded_contexts.rental.application.use_cases.propose_rental_terms import (
    ProposeRentalTermsCommand,
)
from loka.bounded_contexts.rental.application.use_cases.withdraw_rental_application import (
    WithdrawRentalApplicationCommand,
)
from loka.interfaces.http.auth import Principal, require_principal
from loka.interfaces.http.container import get_container
from loka.shared.domain.clock import SystemClock
from loka.shared.infrastructure.composition import (
    confirm_rental_use_case,
    decide_rental_application_use_case,
    express_rental_interest_use_case,
    get_rental_application_use_case,
    propose_rental_terms_use_case,
    unit_of_work,
    withdraw_rental_application_use_case,
)

router = APIRouter(prefix="/rentals", tags=["rentals"])
clock = SystemClock()


class ExpressInterestRequest(BaseModel):
    property_id: uuid.UUID
    message: str | None = Field(default=None, max_length=2000)


class ProposeTermsRequest(BaseModel):
    rent_xaf: int = Field(gt=0)
    deposit_xaf: int | None = Field(default=None, ge=0)
    message: str | None = Field(default=None, max_length=2000)


class DecisionRequest(BaseModel):
    decision: RentalDecision
    reason: str | None = Field(default=None, max_length=2000)


@router.post("/applications", status_code=status.HTTP_201_CREATED)
async def express_interest(
    body: ExpressInterestRequest,
    request: Request,
    principal: Annotated[Principal, Depends(require_principal)],
) -> dict[str, object]:
    database = get_container(request).database
    async with unit_of_work(database) as uow:
        view: RentalApplicationView = await express_rental_interest_use_case(uow).execute(
            ExpressRentalInterestCommand(
                property_id=body.property_id,
                tenant_id=principal.user_id,
                message=body.message,
            ),
            now=clock.now(),
        )
    return view.to_dict()


@router.get("/applications/{application_id}", status_code=status.HTTP_200_OK)
async def get_application(
    application_id: uuid.UUID,
    request: Request,
    principal: Annotated[Principal, Depends(require_principal)],
) -> dict[str, object]:
    database = get_container(request).database
    async with unit_of_work(database) as uow:
        view = await get_rental_application_use_case(uow).execute(
            GetRentalApplicationQuery(
                application_id=application_id, requester_id=principal.user_id
            )
        )
    return view.to_dict()


@router.post("/applications/{application_id}/offers", status_code=status.HTTP_200_OK)
async def propose_terms(
    application_id: uuid.UUID,
    body: ProposeTermsRequest,
    request: Request,
    principal: Annotated[Principal, Depends(require_principal)],
) -> dict[str, object]:
    database = get_container(request).database
    async with unit_of_work(database) as uow:
        view = await propose_rental_terms_use_case(uow).execute(
            ProposeRentalTermsCommand(
                application_id=application_id,
                actor_id=principal.user_id,
                rent=Money(body.rent_xaf),
                deposit=Money(body.deposit_xaf) if body.deposit_xaf is not None else None,
                message=body.message,
            ),
            now=clock.now(),
        )
    return view.to_dict()


@router.post("/applications/{application_id}/decision", status_code=status.HTTP_200_OK)
async def decide(
    application_id: uuid.UUID,
    body: DecisionRequest,
    request: Request,
    principal: Annotated[Principal, Depends(require_principal)],
) -> dict[str, object]:
    database = get_container(request).database
    async with unit_of_work(database) as uow:
        view = await decide_rental_application_use_case(uow).execute(
            DecideRentalApplicationCommand(
                application_id=application_id,
                actor_id=principal.user_id,
                decision=body.decision,
                reason=body.reason,
            ),
            now=clock.now(),
        )
    return view.to_dict()


@router.post("/applications/{application_id}/confirm", status_code=status.HTTP_200_OK)
async def confirm(
    application_id: uuid.UUID,
    request: Request,
    principal: Annotated[Principal, Depends(require_principal)],
) -> dict[str, object]:
    database = get_container(request).database
    async with unit_of_work(database) as uow:
        view = await confirm_rental_use_case(uow).execute(
            ConfirmRentalCommand(
                application_id=application_id, actor_id=principal.user_id
            ),
            now=clock.now(),
        )
    return view.to_dict()


@router.post("/applications/{application_id}/withdraw", status_code=status.HTTP_200_OK)
async def withdraw(
    application_id: uuid.UUID,
    request: Request,
    principal: Annotated[Principal, Depends(require_principal)],
) -> dict[str, object]:
    database = get_container(request).database
    async with unit_of_work(database) as uow:
        view = await withdraw_rental_application_use_case(uow).execute(
            WithdrawRentalApplicationCommand(
                application_id=application_id, actor_id=principal.user_id
            ),
            now=clock.now(),
        )
    return view.to_dict()