"""Property endpoints.

Controllers stay thin: resolve the principal, delegate to a use case, translate
the outcome. No business rule lives here.
"""

from __future__ import annotations

import uuid
from datetime import date
from typing import Annotated, Literal

from fastapi import APIRouter, Depends, Query, Request, status
from pydantic import BaseModel, Field, StrictBool

from loka.bounded_contexts.property.application.queries.search_properties import (
    PropertySearchCriteria,
    PropertySearchQuery,
    SearchPropertiesUseCase,
)
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


@router.get("/search", status_code=status.HTTP_200_OK)
async def search_properties(
    request: Request,
    city: Annotated[str | None, Query()] = None,
    neighbourhood: Annotated[list[str] | None, Query()] = None,
    property_type: Annotated[list[str] | None, Query()] = None,
    min_price_xaf: Annotated[int | None, Query(ge=0)] = None,
    max_price_xaf: Annotated[int | None, Query(ge=0)] = None,
    min_bedrooms: Annotated[int | None, Query(ge=0)] = None,
    min_surface_m2: Annotated[int | None, Query(ge=0)] = None,
    available_before: Annotated[date | None, Query()] = None,
    verified_only: Annotated[bool, Query()] = True,
    sort: Annotated[Literal["recent", "price_asc", "price_desc"], Query()] = "recent",
    cursor: Annotated[str | None, Query()] = None,
    limit: Annotated[int, Query(ge=1, le=50)] = 20,
) -> dict[str, object]:
    """Public listing search.

    Deliberately unauthenticated: the projection only exposes fields that are
    already public on a listing, and browsing a marketplace is anonymous.
    """
    database = get_container(request).database
    query = PropertySearchQuery(
        criteria=PropertySearchCriteria(
            city=city,
            neighbourhoods=tuple(neighbourhood or ()),
            property_types=tuple(property_type or ()),
            min_price_xaf=min_price_xaf,
            max_price_xaf=max_price_xaf,
            min_bedrooms=min_bedrooms,
            min_surface_m2=min_surface_m2,
            available_before=available_before,
            verified_only=verified_only,
            sort=sort,
        ),
        cursor=cursor,
        limit=limit,
    )
    async with unit_of_work(database) as uow:
        result = await SearchPropertiesUseCase(uow).execute(query)
    return {"items": result.items, "next_cursor": result.next_cursor}
