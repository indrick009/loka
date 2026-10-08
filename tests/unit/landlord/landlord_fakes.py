"""In-memory doubles for the landlord unit tests.

Mirrors the SQL repositories' contract so use cases can run without a database:
same lookups, same "latest request for a landlord" ordering.
"""

from __future__ import annotations

import uuid
from types import TracebackType

from loka.bounded_contexts.landlord.domain.entities.landlord_profile import LandlordProfile
from loka.bounded_contexts.landlord.domain.entities.verification_request import (
    VerificationRequest,
)
from loka.shared.application.unit_of_work import UnitOfWork


class FakeLandlordProfileRepository:
    def __init__(self) -> None:
        self.profiles: dict[uuid.UUID, LandlordProfile] = {}

    async def get(self, profile_id: uuid.UUID) -> LandlordProfile | None:
        return self.profiles.get(profile_id)

    async def get_by_user(self, user_id: uuid.UUID) -> LandlordProfile | None:
        for profile in self.profiles.values():
            if profile.user_id == user_id:
                return profile
        return None

    async def add(self, profile: LandlordProfile) -> uuid.UUID:
        self.profiles[profile.profile_id] = profile
        return profile.profile_id

    async def save(self, profile: LandlordProfile) -> None:
        self.profiles[profile.profile_id] = profile


class FakeVerificationRequestRepository:
    def __init__(self) -> None:
        self.requests: dict[uuid.UUID, VerificationRequest] = {}

    async def get(self, request_id: uuid.UUID) -> VerificationRequest | None:
        return self.requests.get(request_id)

    async def add(self, request: VerificationRequest) -> uuid.UUID:
        self.requests[request.request_id] = request
        return request.request_id

    async def save(self, request: VerificationRequest) -> None:
        self.requests[request.request_id] = request

    async def latest_for_landlord(self, landlord_id: uuid.UUID) -> VerificationRequest | None:
        matches = [r for r in self.requests.values() if r.landlord_id == landlord_id]
        if not matches:
            return None
        return max(matches, key=lambda r: r.created_at)


class FakeUnitOfWork(UnitOfWork):
    def __init__(
        self,
        profiles: FakeLandlordProfileRepository,
        requests: FakeVerificationRequestRepository,
    ) -> None:
        self.profiles = profiles
        self.requests = requests
        self.collected: list[object] = []
        self.commits = 0

    def __enter__(self) -> FakeUnitOfWork:
        return self

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        tb: TracebackType | None,
    ) -> None:
        return None

    async def __aenter__(self) -> FakeUnitOfWork:
        return self

    async def __aexit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        tb: TracebackType | None,
    ) -> None:
        return None

    async def commit(self) -> None:
        self.commits += 1

    async def rollback(self) -> None:
        return None

    @property
    def events(self) -> object:
        return None

    def collect(self, aggregate: object) -> None:
        self.collected.append(aggregate)

    def repository(self, name: str) -> object:
        from loka.bounded_contexts.landlord.domain.repositories.verification_repository import (
            LANDLORD_PROFILE_REPOSITORY,
            VERIFICATION_REQUEST_REPOSITORY,
        )

        if name == LANDLORD_PROFILE_REPOSITORY:
            return self.profiles
        if name == VERIFICATION_REQUEST_REPOSITORY:
            return self.requests
        raise LookupError(f"unexpected repository: {name}")