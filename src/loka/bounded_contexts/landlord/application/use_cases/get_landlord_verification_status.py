"""Read the current landlord verification state for an account."""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import datetime

from loka.bounded_contexts.landlord.application.dto.landlord_verification_dto import (
    LandlordStatusView,
)
from loka.bounded_contexts.landlord.domain.repositories.verification_repository import (
    LANDLORD_PROFILE_REPOSITORY,
    VERIFICATION_REQUEST_REPOSITORY,
)
from loka.shared.application.unit_of_work import UnitOfWork
from loka.shared.application.use_case import UseCase
from loka.shared.domain.errors import ResourceNotFound


@dataclass(frozen=True, slots=True)
class LandlordStatusQuery:
    user_id: uuid.UUID


class GetLandlordVerificationStatusUseCase(UseCase[LandlordStatusQuery, LandlordStatusView]):
    name = "landlord.get_status"

    def __init__(self, uow: UnitOfWork) -> None:
        super().__init__(uow)
        self._profiles = uow.repository(LANDLORD_PROFILE_REPOSITORY)
        self._requests = uow.repository(VERIFICATION_REQUEST_REPOSITORY)

    async def execute(
        self, query: LandlordStatusQuery, *, now: datetime | None = None
    ) -> LandlordStatusView:
        profile = await self._profiles.get_by_user(query.user_id)
        if profile is None:
            raise ResourceNotFound(
                "no landlord profile for this account",
                context={"user_id": str(query.user_id)},
            )
        request = await self._requests.latest_for_landlord(profile.profile_id)
        return LandlordStatusView.from_aggregate(profile, request)