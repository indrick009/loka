"""Landlord profile: who owns a listing.

A directory entry, nothing more. It is created the first time an account
lists a property, and a listing names the profile rather than the account.
Identity verification was dropped on 27 October — no step of the listing flow
asks for a CNI, so the profile carries no entitlement to check.
"""

from __future__ import annotations

import uuid
from datetime import datetime

from loka.shared.domain.aggregate import AggregateRoot
from loka.shared.domain.clock import ensure_utc


class LandlordProfile(AggregateRoot):
    __slots__ = ("created_at", "display_name", "profile_id", "updated_at", "user_id")

    def __init__(
        self,
        *,
        profile_id: uuid.UUID,
        user_id: uuid.UUID,
        display_name: str | None,
        now: datetime,
    ) -> None:
        super().__init__(aggregate_type="LandlordProfile")
        self._assign_id(profile_id)
        self.profile_id = profile_id
        self.user_id = user_id
        self.display_name = (display_name or "").strip()[:80] or None
        self.created_at = ensure_utc(now)
        self.updated_at = ensure_utc(now)
