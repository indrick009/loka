"""Landlord profile repository port (domain-facing)."""

from __future__ import annotations

from typing import Protocol

from loka.bounded_contexts.housing.domain.entities.landlord_profile import LandlordProfile

LANDLORD_PROFILE_REPOSITORY = "landlord_profile"


class LandlordProfileRepository(Protocol):
    async def add(self, profile: LandlordProfile) -> None: ...
