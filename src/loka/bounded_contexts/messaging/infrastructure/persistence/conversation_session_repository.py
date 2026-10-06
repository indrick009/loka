"""Conversation session persistence.

Sessions are read through their phone number rather than an in-memory session
cache: the conversational state must survive a worker crash, a deploy or a
reconnect, which is only possible if the current step is always durable.
"""

from __future__ import annotations

import uuid

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from loka.bounded_contexts.identity.domain.value_objects.phone_number import PhoneNumber
from loka.bounded_contexts.messaging.domain.entities.conversation_session import (
    ConversationSession,
    FlowName,
    FlowStep,
    SessionStatus,
)
from loka.bounded_contexts.messaging.infrastructure.persistence.models import (
    ConversationSessionRow,
)
from loka.shared.domain.errors import ConcurrencyConflict


class SqlAlchemyConversationSessionRepository:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def get_by_id(self, session_id: uuid.UUID) -> ConversationSession | None:
        row = await self._session.get(ConversationSessionRow, session_id)
        return None if row is None else _to_domain(row)

    async def latest_by_phone(self, phone: PhoneNumber) -> ConversationSession | None:
        result = await self._session.execute(
            select(ConversationSessionRow)
            .where(ConversationSessionRow.phone_e164 == phone.e164)
            .order_by(ConversationSessionRow.started_at.desc(), ConversationSessionRow.id.desc())
            .limit(1)
        )
        row = result.scalars().first()
        if row is None:
            return None
        return _to_domain(row)

    async def add(self, session: ConversationSession) -> None:
        self._session.add(_to_row(session))

    async def save(
        self, session: ConversationSession, *, expected_revision: int | None = None
    ) -> None:
        row = await self._session.get(ConversationSessionRow, session.id)
        if row is None:
            await self.add(session)
            return
        if expected_revision is not None and row.revision != expected_revision:
            raise ConcurrencyConflict(
                "conversation session was modified concurrently",
                context={
                    "session_id": str(session.id),
                    "expected_revision": expected_revision,
                    "actual_revision": row.revision,
                },
            )
        _apply(row, session)


def _to_domain(row: ConversationSessionRow) -> ConversationSession:
    session = ConversationSession(
        session_id=row.id,
        user_id=row.user_id,
        phone=PhoneNumber(e164=row.phone_e164),
        now=row.started_at,
        flow=FlowName(row.flow),
    )
    session.step = FlowStep(row.step)
    session.status = SessionStatus(row.status)
    session.context = dict(row.context_json)
    session.language = row.language
    session.revision = row.revision
    session.assigned_property_id = row.assigned_property_id
    session.last_step_at = row.last_step_at
    session.updated_at = row.updated_at
    # The row is the baseline: nothing is pending, so loading must not make the
    # next save think it has to bump the aggregate version.
    session.mark_persisted(row.revision)
    return session


def _to_row(session: ConversationSession) -> ConversationSessionRow:
    row = ConversationSessionRow(
        id=session.id,
        user_id=session.user_id,
        phone_e164=session.phone.e164,
        flow=session.flow.value,
        step=session.step.value,
        status=session.status.value,
        context_json=dict(session.context),
        language=session.language,
        revision=session.revision,
        assigned_property_id=session.assigned_property_id,
        last_step_at=session.last_step_at,
        started_at=session.started_at,
        updated_at=session.updated_at,
    )
    return row


def _apply(row: ConversationSessionRow, session: ConversationSession) -> None:
    """Copy the mutable state onto an attached row.

    The phone number is deliberately absent: it is the session's identity
    anchor and never changes for a given session.
    """

    row.user_id = session.user_id
    row.flow = session.flow.value
    row.step = session.step.value
    row.status = session.status.value
    row.context_json = dict(session.context)
    row.language = session.language
    row.revision = session.revision
    row.assigned_property_id = session.assigned_property_id
    row.last_step_at = session.last_step_at
    row.updated_at = session.updated_at
