"""Ports exposed by the Landlord context to the other contexts."""

from __future__ import annotations

import uuid
from typing import Protocol, runtime_checkable


@runtime_checkable
class LandlordDirectory(Protocol):
    """Read-only view of a landlord's verification state.

    Consuming contexts (Property, Rental) depend on this port instead of the
    verification tables, so verification can be proven in more than one way
    without touching them.
    """

    async def is_verified(self, landlord_id: uuid.UUID) -> bool: ...

    async def profile_id_for_user(self, user_id: uuid.UUID) -> uuid.UUID | None:
        """Landlord profile owning this user, if any.

        Callers authenticate with a *user* id while listings carry a *profile*
        id, so ownership can only be decided through this lookup.
        """
