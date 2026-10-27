"""Ports the Housing context exposes to the others."""

from __future__ import annotations

import uuid
from typing import Protocol, runtime_checkable


@runtime_checkable
class LandlordDirectory(Protocol):
    """Who owns a listing, in both directions."""

    async def profile_id_for_user(self, user_id: uuid.UUID) -> uuid.UUID | None:
        """Landlord profile owning this user, if any.

        Callers authenticate with a *user* id while listings carry a *profile*
        id, so ownership can only be decided through this lookup.
        """

    async def user_id_for_profile(self, landlord_id: uuid.UUID) -> uuid.UUID | None:
        """The account behind a listing's landlord.

        The other direction, and needed for the same reason: a listing names a
        profile, while writing to its owner on WhatsApp needs the account that
        holds the phone number.
        """
