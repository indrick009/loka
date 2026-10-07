"""Verification repository ports (domain-facing).

Writing a verification request and the profile it entitles happen in the same
transaction, but through two ports: the request owns the evidence and the
decision, the profile the entitlement publication depends on.
"""

from __future__ import annotations

import uuid
from typing import Protocol

from loka.bounded_contexts.landlord.domain.entities.landlord_profile import LandlordProfile
from loka.bounded_contexts.landlord.domain.entities.verification_request import (
    VerificationRequest,
)

LANDLORD_PROFILE_REPOSITORY = "landlord_profile"
VERIFICATION_REQUEST_REPOSITORY = "landlord_verification_request"


class LandlordProfileRepository(Protocol):
    async def get(self, profile_id: uuid.UUID) -> LandlordProfile | None: ...

    async def get_by_user(self, user_id: uuid.UUID) -> LandlordProfile | None: ...

    async def add(self, profile: LandlordProfile) -> None: ...

    async def save(self, profile: LandlordProfile, *, expected_version: int | None = None) -> None:
        """Raise ``ConcurrencyConflict`` when the version no longer matches."""


class VerificationRequestRepository(Protocol):
    async def get(self, request_id: uuid.UUID) -> VerificationRequest | None: ...

    async def latest_for_landlord(self, landlord_id: uuid.UUID) -> VerificationRequest | None:
        """The most recent request, regardless of status, for history continuity."""

    async def add(self, request: VerificationRequest) -> None: ...

    async def save(
        self, request: VerificationRequest, *, expected_version: int | None = None
    ) -> None:
        """Raise ``ConcurrencyConflict`` when the version no longer matches."""