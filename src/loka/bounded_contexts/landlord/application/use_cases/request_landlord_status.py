"""Open the landlord journey for an account.

A tenant asks to become a landlord: this creates the profile that publication
will later gate on and the first (empty) verification request that will carry
the evidence. Idempotent: calling it again on an existing account returns the
current state instead of minting a second profile.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import datetime

from loka.bounded_contexts.landlord.application.dto.landlord_verification_dto import (
    LandlordStatusView,
)
from loka.bounded_contexts.landlord.domain.entities.landlord_profile import LandlordProfile
from loka.bounded_contexts.landlord.domain.entities.verification_request import (
    VerificationRequest,
)
from loka.bounded_contexts.landlord.domain.repositories.verification_repository import (
    LANDLORD_PROFILE_REPOSITORY,
    VERIFICATION_REQUEST_REPOSITORY,
)
from loka.shared.application.unit_of_work import UnitOfWork
from loka.shared.application.use_case import UseCase
from loka.shared.domain.identifiers import new_id


@dataclass(frozen=True, slots=True)
class RequestLandlordStatusCommand:
    user_id: uuid.UUID
    display_name: str | None = None


class RequestLandlordStatusUseCase(UseCase[RequestLandlordStatusCommand, LandlordStatusView]):
    name = "landlord.request_status"

    def __init__(self, uow: UnitOfWork) -> None:
        super().__init__(uow)
        self._profiles = uow.repository(LANDLORD_PROFILE_REPOSITORY)
        self._requests = uow.repository(VERIFICATION_REQUEST_REPOSITORY)

    async def execute(  # type: ignore[override]
        self, command: RequestLandlordStatusCommand, *, now: datetime
    ) -> LandlordStatusView:
        profile = await self._profiles.get_by_user(command.user_id)
        request: VerificationRequest | None = None
        if profile is None:
            profile = LandlordProfile(
                profile_id=new_id(),
                user_id=command.user_id,
                display_name=command.display_name,
                now=now,
            )
            await self._profiles.add(profile)
        elif command.display_name:
            profile.rename(command.display_name, now=now)

        if not profile.is_verified:
            request = await self._requests.latest_for_landlord(profile.profile_id)
            if request is None:
                request = VerificationRequest(
                    request_id=new_id(),
                    landlord_id=profile.profile_id,
                    user_id=command.user_id,
                    now=now,
                )
                await self._requests.add(request)

        self._uow.collect(profile)
        if request is not None:
            self._uow.collect(request)
        await self._uow.commit()
        self._log("landlord_status_requested", profile_id=str(profile.profile_id))
        return LandlordStatusView.from_aggregate(profile, request)