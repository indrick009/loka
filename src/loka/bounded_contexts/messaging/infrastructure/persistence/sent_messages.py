"""What Kora actually sent, one row per delivered message.

The outbound layer renders the final sentence — a template, a model's text or
its rewrite — so it is the only place that knows the words the user read. Kept
because a conversation needs both sides: without Kora's lines the model could
not tell what "le premier là" or "comme tu as dit" points at.
"""

from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession

from loka.bounded_contexts.messaging.infrastructure.persistence.models import (
    OutboundMessageRow,
)
from loka.shared.domain.clock import ensure_utc


class SqlAlchemySentMessageLog:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def record(
        self,
        *,
        idempotency_key: str,
        session_id: uuid.UUID | None,
        recipient_phone: str,
        text: str,
        now: datetime,
    ) -> None:
        """Idempotent on the event: a redelivered send is not a second line."""
        moment = ensure_utc(now)
        await self._session.execute(
            insert(OutboundMessageRow)
            .values(
                id=uuid.uuid4(),
                idempotency_key=idempotency_key,
                session_id=session_id,
                conversation_ref=recipient_phone,
                recipient_phone=recipient_phone,
                text=text,
                status="SENT",
                attempt_count=1,
                queued_at=moment,
                sent_at=moment,
            )
            .on_conflict_do_nothing(index_elements=["idempotency_key"])
        )
