"""Authorisation helpers for visits."""

from __future__ import annotations

import uuid

from loka.bounded_contexts.landlord.application.ports import LandlordDirectory
from loka.bounded_contexts.visit.domain.entities.visit import Visit
from loka.shared.domain.errors import AuthorizationDenied


async def is_landlord_of(
    landlords: LandlordDirectory, visit: Visit, actor_id: uuid.UUID
) -> bool:
    profile_id = await landlords.profile_id_for_user(actor_id)
    return profile_id is not None and profile_id == visit.landlord_id


async def require_party(
    landlords: LandlordDirectory, visit: Visit, actor_id: uuid.UUID
) -> None:
    if actor_id == visit.tenant_id:
        return
    if await is_landlord_of(landlords, visit, actor_id):
        return
    raise AuthorizationDenied(
        "only a party to the visit may act on it",
        context={"visit_id": str(visit.visit_id)},
    )


async def require_landlord(
    landlords: LandlordDirectory, visit: Visit, actor_id: uuid.UUID
) -> None:
    if not await is_landlord_of(landlords, visit, actor_id):
        raise AuthorizationDenied(
            "only the landlord of this listing may confirm a visit",
            context={"visit_id": str(visit.visit_id)},
        )