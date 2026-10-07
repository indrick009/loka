"""SQLAlchemy implementation of the landlord verification write model.

Two adapters, two protocols: a profile is a long-lived entitlement, a request
is one attempt at obtaining it, and ``add``/``save`` mean different things for
each, so they must not share a virtual method name.
"""

from __future__ import annotations

import uuid

from sqlalchemy import delete, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from loka.bounded_contexts.landlord.domain.entities.landlord_profile import LandlordProfile
from loka.bounded_contexts.landlord.domain.entities.verification_request import (
    VerificationRequest,
)
from loka.bounded_contexts.landlord.infrastructure.mappers.verification_mapper import (
    document_to_row,
    profile_from_row,
    profile_to_row,
    request_from_row,
    request_to_row,
)
from loka.bounded_contexts.landlord.infrastructure.persistence.models import (
    LandlordProfileRow,
    VerificationDocumentRow,
    VerificationRequestRow,
)
from loka.shared.application.unit_of_work import SupportsAfterCommit
from loka.shared.domain.errors import ConcurrencyConflict


class SqlAlchemyLandlordProfileRepository:
    def __init__(
        self,
        session: AsyncSession,
        *,
        unit_of_work: SupportsAfterCommit | None = None,
    ) -> None:
        self._session = session
        self._unit_of_work = unit_of_work

    async def get(self, profile_id: uuid.UUID) -> LandlordProfile | None:
        row = await self._session.get(LandlordProfileRow, profile_id)
        return profile_from_row(row) if row is not None else None

    async def get_by_user(self, user_id: uuid.UUID) -> LandlordProfile | None:
        result = await self._session.execute(
            select(LandlordProfileRow).where(LandlordProfileRow.user_id == user_id)
        )
        row = result.scalar_one_or_none()
        return profile_from_row(row) if row is not None else None

    async def add(self, profile: LandlordProfile) -> None:
        self._session.add(LandlordProfileRow(**profile_to_row(profile)))
        self._mark_persisted(profile)

    async def save(
        self, profile: LandlordProfile, *, expected_version: int | None = None
    ) -> None:
        """Optimistic locking: the write only lands if nobody moved the row."""
        guard = expected_version if expected_version is not None else profile.persisted_version
        payload = profile_to_row(profile)
        payload.pop("id")
        payload.pop("created_at")
        payload["revision"] = profile.version
        stmt = (
            update(LandlordProfileRow)
            .where(LandlordProfileRow.id == profile.id)
            .where(LandlordProfileRow.revision == guard)
            .values(**payload)
            .returning(LandlordProfileRow.id)
        )
        result = await self._session.execute(stmt)
        if result.scalar_one_or_none() is None:
            raise ConcurrencyConflict(
                "landlord profile was modified concurrently",
                context={"profile_id": str(profile.id), "expected_version": guard},
            )
        self._mark_persisted(profile)

    def _mark_persisted(self, profile: LandlordProfile) -> None:
        version = profile.version
        if self._unit_of_work is None:
            profile.mark_persisted(version)
            return
        self._unit_of_work.after_commit(lambda: profile.mark_persisted(version))


class SqlAlchemyVerificationRequestRepository:
    def __init__(
        self,
        session: AsyncSession,
        *,
        unit_of_work: SupportsAfterCommit | None = None,
    ) -> None:
        self._session = session
        self._unit_of_work = unit_of_work

    async def get(self, request_id: uuid.UUID) -> VerificationRequest | None:
        row = await self._session.get(VerificationRequestRow, request_id)
        if row is None:
            return None
        return request_from_row(row, await self._documents(request_id))

    async def latest_for_landlord(self, landlord_id: uuid.UUID) -> VerificationRequest | None:
        result = await self._session.execute(
            select(VerificationRequestRow)
            .where(VerificationRequestRow.landlord_id == landlord_id)
            .order_by(VerificationRequestRow.created_at.desc())
            .limit(1)
        )
        row = result.scalar_one_or_none()
        if row is None:
            return None
        return request_from_row(row, await self._documents(row.id))

    async def add(self, request: VerificationRequest) -> None:
        self._session.add(VerificationRequestRow(**request_to_row(request)))
        self._mark_persisted(request)
        await self._replace_documents(request)

    async def save(
        self, request: VerificationRequest, *, expected_version: int | None = None
    ) -> None:
        guard = expected_version if expected_version is not None else request.persisted_version
        payload = request_to_row(request)
        payload.pop("id")
        payload.pop("created_at")
        payload["revision"] = request.version
        stmt = (
            update(VerificationRequestRow)
            .where(VerificationRequestRow.id == request.id)
            .where(VerificationRequestRow.revision == guard)
            .values(**payload)
            .returning(VerificationRequestRow.id)
        )
        result = await self._session.execute(stmt)
        if result.scalar_one_or_none() is None:
            raise ConcurrencyConflict(
                "verification request was modified concurrently",
                context={"request_id": str(request.id), "expected_version": guard},
            )
        self._mark_persisted(request)
        await self._replace_documents(request)

    def _mark_persisted(self, request: VerificationRequest) -> None:
        version = request.version
        if self._unit_of_work is None:
            request.mark_persisted(version)
            return
        self._unit_of_work.after_commit(lambda: request.mark_persisted(version))

    async def _documents(self, request_id: uuid.UUID) -> list[VerificationDocumentRow]:
        result = await self._session.execute(
            select(VerificationDocumentRow)
            .where(VerificationDocumentRow.request_id == request_id)
            .order_by(VerificationDocumentRow.created_at)
        )
        return list(result.scalars())

async def _replace_documents(self, request: VerificationRequest) -> None:
        """Rewrite the evidence set so the rows match the aggregate exactly."""
        await self._session.execute(
            delete(VerificationDocumentRow).where(
                VerificationDocumentRow.request_id == request.id
            )
        )
        for document in request.documents:
            self._session.add(
                VerificationDocumentRow(**document_to_row(document, request.id))
            )
        await self._session.flush()