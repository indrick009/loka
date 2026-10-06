"""Payment endpoints.

Initiation and status are read/written by the authenticated tenant. The webhook
is unauthenticated by design: the provider does not log in, it submits a signed
body, and only the gateway adapter decides what the signature proves.
"""

from __future__ import annotations

import uuid
from typing import Annotated

from fastapi import APIRouter, Depends, Request, status
from pydantic import BaseModel

from loka.bounded_contexts.payment.application.use_cases.get_service_access_status import (
    ServiceAccessQuery,
)
from loka.bounded_contexts.payment.application.use_cases.handle_payment_callback import (
    HandlePaymentCallbackCommand,
)
from loka.bounded_contexts.payment.application.use_cases.initiate_service_fee import (
    InitiateServiceFeeCommand,
)
from loka.interfaces.http.auth import Principal, require_principal
from loka.interfaces.http.container import get_container
from loka.shared.domain.clock import SystemClock
from loka.shared.infrastructure.composition import (
    get_service_access_status_use_case,
    handle_payment_callback_use_case,
    initiate_service_fee_use_case,
    unit_of_work,
)

router = APIRouter(tags=["payments"])
clock = SystemClock()


class InitiateServiceFeeRequest(BaseModel):
    # The rental context (Phase 7) does not exist yet; until then the landlord
    # who owns the application is supplied explicitly by the tenant.
    landlord_id: uuid.UUID


@router.post(
    "/payments/applications/{application_id}/initiate",
    status_code=status.HTTP_201_CREATED,
)
async def initiate_service_fee(
    application_id: uuid.UUID,
    body: InitiateServiceFeeRequest,
    request: Request,
    principal: Annotated[Principal, Depends(require_principal)],
) -> dict[str, object]:
    database = get_container(request).database
    settings = get_container(request).settings
    async with unit_of_work(database) as uow:
        view = await initiate_service_fee_use_case(uow, settings).execute(
            InitiateServiceFeeCommand(
                application_id=application_id,
                tenant_id=principal.user_id,
                landlord_id=body.landlord_id,
            ),
            now=clock.now(),
        )
    return view.to_dict()


@router.get("/payments/applications/{application_id}", status_code=status.HTTP_200_OK)
async def get_service_access_status(
    application_id: uuid.UUID,
    request: Request,
    principal: Annotated[Principal, Depends(require_principal)],
) -> dict[str, object]:
    database = get_container(request).database
    async with unit_of_work(database) as uow:
        result = await get_service_access_status_use_case(uow).execute(
            ServiceAccessQuery(
                application_id=application_id,
                tenant_id=principal.user_id,
            ),
            now=clock.now(),
        )
    return result.to_dict()


@router.post(
    "/webhooks/payments/{provider}",
    status_code=status.HTTP_200_OK,
    include_in_schema=False,
)
async def handle_payment_callback(
    provider: str,
    request: Request,
) -> dict[str, object]:
    raw_body = await request.body()
    signature = request.headers.get("x-loka-signature") or ""
    database = get_container(request).database
    settings = get_container(request).settings

    async def _submit() -> dict[str, object]:
        async with unit_of_work(database) as uow:
            outcome = await handle_payment_callback_use_case(uow, settings).execute(
                HandlePaymentCallbackCommand(
                    provider=provider,
                    provider_event_id="",
                    raw_body=raw_body,
                    signature=signature,
                    headers=dict(request.headers),
                ),
                now=clock.now(),
            )
        return outcome.to_dict()

    # A provider signature failure is a domain rejection and must reach the
    # caller without going through the generic exception handler twice.
    return await _submit()