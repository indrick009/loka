"""Inbound message persistence.

De-duplication is a database concern here on purpose. The gateway may deliver
the same message twice within milliseconds and two workers can consume it in
parallel, so a read-then-write check in the use case would leave a window open:
both workers would see "not seen yet" and both would write. ``ON CONFLICT DO
NOTHING`` against ``uq_inbound_external_message`` closes that window at the only
place where it can be closed safely.
"""

from __future__ import annotations

import uuid

from sqlalchemy import select, update
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession

from loka.bounded_contexts.messaging.domain.repositories.inbound_message_repository import (
    StoredInboundMessage,
)
from loka.bounded_contexts.messaging.domain.value_objects.inbound_message import InboundMessage
from loka.bounded_contexts.messaging.infrastructure.persistence.models import InboundMessageRow


class SqlAlchemyInboundMessageRepository:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def get_by_external_id(
        self, *, provider: str, external_message_id: str
    ) -> StoredInboundMessage | None:
        result = await self._session.execute(
            select(
                InboundMessageRow.id,
                InboundMessageRow.session_id,
                InboundMessageRow.sender_user_id,
                InboundMessageRow.processed,
            ).where(
                InboundMessageRow.provider == provider,
                InboundMessageRow.external_message_id == external_message_id,
            )
        )
        row = result.one_or_none()
        if row is None:
            return None
        return StoredInboundMessage(
            message_id=row[0],
            session_id=row[1],
            sender_user_id=row[2],
            processed=bool(row[3]),
        )

    async def get_by_id(self, message_id: uuid.UUID) -> StoredInboundMessage | None:
        result = await self._session.execute(
            select(
                InboundMessageRow.id,
                InboundMessageRow.session_id,
                InboundMessageRow.sender_user_id,
                InboundMessageRow.processed,
                InboundMessageRow.text,
            ).where(InboundMessageRow.id == message_id)
        )
        row = result.one_or_none()
        if row is None:
            return None
        return StoredInboundMessage(
            message_id=row[0],
            session_id=row[1],
            sender_user_id=row[2],
            processed=bool(row[3]),
            text=row[4],
        )

    async def add_if_absent(self, message: InboundMessage) -> StoredInboundMessage | None:
        statement = (
            pg_insert(InboundMessageRow)
            .values(
                id=message.message_id,
                external_message_id=message.external_message_id,
                provider=message.provider,
                conversation_ref=message.conversation_ref,
                sender_phone=message.sender_phone.e164,
                # Resolved after the dedupe check so a redelivery never causes a
                # lookup or a session to be created.
                sender_user_id=None,
                session_id=None,
                message_kind=message.kind.value,
                text=message.text,
                media_object_key=message.media_object_key,
                processed=False,
                provider_timestamp=message.provider_timestamp,
                received_at=message.received_at,
                metadata_json=dict(message.metadata),
            )
            # Column inference, not ON CONSTRAINT: the schema carries this as a
            # unique *index* (uq_inbound_external_message), and ON CONFLICT ON
            # CONSTRAINT only resolves named constraints.
            .on_conflict_do_nothing(index_elements=["provider", "external_message_id"])
            .returning(InboundMessageRow.id)
        )
        result = await self._session.execute(statement)
        inserted = result.scalar_one_or_none()
        if inserted is None:
            return None
        return StoredInboundMessage(
            message_id=inserted, session_id=None, sender_user_id=None, processed=False
        )

    async def attach_conversation(
        self,
        message_id: uuid.UUID,
        *,
        session_id: uuid.UUID | None,
        sender_user_id: uuid.UUID | None,
    ) -> None:
        await self._session.execute(
            update(InboundMessageRow)
            .where(InboundMessageRow.id == message_id)
            .values(session_id=session_id, sender_user_id=sender_user_id)
        )

    async def mark_processed(self, message_id: uuid.UUID) -> None:
        """Flag a message as handled so a resumed pipeline can skip it."""
        await self._session.execute(
            update(InboundMessageRow)
            .where(InboundMessageRow.id == message_id)
            .values(processed=True)
        )
