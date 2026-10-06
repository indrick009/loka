"""Identity's ``UserDirectory`` adapter.

The lookup goes through the keyed blind index rather than the plaintext phone
column: the index is the only value designed to stay searchable once
``users.phone_e164`` is actually encrypted, so building the directory on it
means encryption will not silently break every inbound message.
"""

from __future__ import annotations

import uuid

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from loka.bounded_contexts.identity.domain.value_objects.phone_number import PhoneNumber
from loka.bounded_contexts.identity.infrastructure.persistence.models import UserRow
from loka.shared.infrastructure.crypto.blind_index import blind_index


class SqlAlchemyUserDirectory:
    def __init__(self, session: AsyncSession, *, index_key: bytes) -> None:
        self._session = session
        self._index_key = index_key

    async def user_id_for_phone(self, phone: PhoneNumber) -> uuid.UUID | None:
        result = await self._session.execute(
            select(UserRow.id).where(
                UserRow.phone_e164_hash == blind_index(phone.e164, key=self._index_key)
            )
        )
        return result.scalar_one_or_none()
