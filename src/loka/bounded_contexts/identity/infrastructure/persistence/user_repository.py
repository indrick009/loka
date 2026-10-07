"""SQLAlchemy adapter for the identity read and provisioning ports.

Backed by the keyed blind index: ``users.phone_e164`` is meant to become
encrypted at the column level, so the hash is the only value that stays
searchable and both ports look an account up through it, never the plaintext.
"""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from loka.bounded_contexts.identity.domain.entities.user import Role, User
from loka.bounded_contexts.identity.domain.value_objects.phone_number import PhoneNumber
from loka.bounded_contexts.identity.infrastructure.persistence.models import UserRow
from loka.shared.application.unit_of_work import UnitOfWork
from loka.shared.infrastructure.crypto.blind_index import blind_index


class SqlAlchemyUserRepository:
    def __init__(
        self,
        session: AsyncSession,
        *,
        index_key: bytes,
        unit_of_work: UnitOfWork | None = None,
    ) -> None:
        self._session = session
        self._index_key = index_key
        self._unit_of_work = unit_of_work

    async def user_id_for_phone(self, phone: PhoneNumber) -> uuid.UUID | None:
        result = await self._session.execute(
            select(UserRow.id).where(UserRow.phone_e164_hash == self._key(phone))
        )
        return result.scalar_one_or_none()

    async def ensure_user(self, phone: PhoneNumber, *, now: datetime) -> uuid.UUID:
        """Return the account for ``phone``, creating it on first contact.

        First contact is a WhatsApp message, which proves the sender holds the
        number, so the account is active immediately and no OTP is sent. The
        insert runs inside a savepoint: two workers can race on a brand-new
        number, and the loser must resolve to the winner's row instead of
        aborting the whole message transaction.
        """
        existing = await self.user_id_for_phone(phone)
        if existing is not None:
            return existing

        user = User(
            user_id=uuid.uuid4(),
            phone=phone,
            display_name=None,
            now=now,
            roles=frozenset({Role.TENANT}),
        )
        user.confirm_phone(now=now)
        try:
            async with self._session.begin_nested():
                self._session.add(UserRow(**self._to_row(user, phone)))
                await self._session.flush()
        except IntegrityError:
            winner = await self.user_id_for_phone(phone)
            if winner is None:  # pragma: no cover - the unique key cannot vanish
                raise
            return winner

        if self._unit_of_work is not None:
            self._unit_of_work.collect(user)
        return user.user_id

    def _key(self, phone: PhoneNumber) -> str:
        return blind_index(phone.e164, key=self._index_key)

    def _to_row(self, user: User, phone: PhoneNumber) -> dict[str, Any]:
        return {
            "id": user.user_id,
            "phone_e164": user.phone.e164,
            "phone_e164_hash": self._key(phone),
            "display_name": user.display_name,
            "roles": sorted(role.value for role in user.roles),
            "status": user.status.value,
            "phone_verified_at": user.phone_verified_at,
            "last_seen_at": user.last_seen_at,
            "suspension_reason": user.suspension_reason,
            "locale": "fr",
            "created_at": user.created_at,
            "updated_at": user.updated_at,
        }