"""Property endpoints.

Controllers stay thin: resolve the principal, delegate to a use case, translate
the outcome. No business rule lives here.
"""

from __future__ import annotations

import uuid
from typing import Annotated

from fastapi import APIRouter, Depends, Request, status
from pydantic import BaseModel, Field, StrictBool

from loka.bounded_contexts.property.application.use_cases.publish_property import (
    PublishPropertyCommand,
)
from loka.interfaces.http.auth import Principal, require_principal
from loka.interfaces.http.container import get_container
from loka.shared.domain.clock import SystemClock
from loka.shared.infrastructure.composition import publish_property_use_case, unit_of_work

router = APIRouter(prefix="/properties", tags=["properties"])
clock = SystemClock()


class PublishPropertyRequest(BaseModel):
    # StrictBool, not bool: pydantic coerces "yes"/1/on to True, and consent to
    # publish someone's listing must be an explicit JSON boolean.
    confirmed_by_landlord: StrictBool = Field(
        description=(
            "Explicit confirmation that the landlord approved this listing. The "
            "server never infers consent from a draft being complete."
        )
    )


@router.post("/{property_id}/publish", status_code=status.HTTP_200_OK)
async def publish_property(
    property_id: uuid.UUID,
    body: PublishPropertyRequest,
    request: Request,
    principal: Annotated[Principal, Depends(require_principal)],
) -> dict[str, object]:
    database = get_container(request).database
    async with unit_of_work(database) as uow:
        summary = await publish_property_use_case(uow).execute(
            PublishPropertyCommand(
                property_id=property_id,
                actor_id=principal.user_id,
                confirmed_by_landlord=body.confirmed_by_landlord,
            ),
            now=clock.now(),
        )
    return summary.to_dict()
