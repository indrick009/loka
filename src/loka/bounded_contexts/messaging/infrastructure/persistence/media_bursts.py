"""Whether a photo is the last of a burst.

WhatsApp delivers an album of ten photos as ten separate messages. Each is
attached on arrival, and each used to be answered: ten "photo bien reçue" in a
row. The acknowledgement now waits a moment, then asks this question — and
only the photo nobody sent anything after is answered.
"""

from __future__ import annotations

import uuid

from sqlalchemy import exists, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import aliased

from loka.bounded_contexts.messaging.infrastructure.persistence.models import (
    InboundMessageRow,
)


class SqlAlchemyMediaBursts:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def has_newer_media(self, message_id: uuid.UUID) -> bool:
        """A later photo, video or file in the same thread than ``message_id``."""
        this = aliased(InboundMessageRow)
        later = aliased(InboundMessageRow)
        statement = select(
            exists().where(
                this.id == message_id,
                later.session_id == this.session_id,
                later.media_object_key.is_not(None),
                later.received_at > this.received_at,
            )
        )
        return bool((await self._session.execute(statement)).scalar())
