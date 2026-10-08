"""Authorisation helpers for rental decisions.

Tenants authenticate as users, landlords as profiles. A rental application
stores the landlord *profile* id, so deciding whether an actor is the landlord
must go through the directory, exactly as property publication does.
"""

from __future__ import annotations

import uuid

from loka.bounded_contexts.landlord.application.ports import LandlordDirectory
from loka.bounded_contexts.rental.domain.entities.rental_application import (
    RentalApplication,
)
from loka.shared.domain.errors import AuthorizationDenied


async def is_landlord_of(
    landlords: LandlordDirectory, application: RentalApplication, actor_id: uuid.UUID
) -> bool:
    profile_id = await landlords.profile_id_for_user(actor_id)
    return profile_id is not None and profile_id == application.landlord_id


async def require_landlord(
    landlords: LandlordDirectory, application: RentalApplication, actor_id: uuid.UUID
) -> None:
    if not await is_landlord_of(landlords, application, actor_id):
        raise AuthorizationDenied(
            "only the landlord of this listing may decide on the application",
            context={"application_id": str(application.application_id)},
        )


async def require_party(
    landlords: LandlordDirectory, application: RentalApplication, actor_id: uuid.UUID
) -> None:
    """Allow the tenant or the landlord, reject everyone else."""
    if actor_id == application.tenant_id:
        return
    if await is_landlord_of(landlords, application, actor_id):
        return
    raise AuthorizationDenied(
        "only a party to the application may act on it",
        context={"application_id": str(application.application_id)},
    )


def require_tenant(application: RentalApplication, actor_id: uuid.UUID) -> None:
    if actor_id != application.tenant_id:
        raise AuthorizationDenied(
            "only the tenant may act on their application",
            context={"application_id": str(application.application_id)},
        )