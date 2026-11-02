"""The engine's side of ``PlugDesk``: sessions moved, messages staged."""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any

from loka.bounded_contexts.auth.domain.value_objects.phone_number import PhoneNumber
from loka.bounded_contexts.conversation.application.plugs.turn import PlugTurn, Steer
from loka.bounded_contexts.conversation.domain.entities.conversation_session import (
    ConversationSession,
    FlowStep,
)
from loka.bounded_contexts.conversation.domain.events.message_events import (
    WhatsAppMessageSendRequested,
)
from loka.bounded_contexts.conversation.domain.repositories.conversation_session_repository import (
    ConversationSessionRepository,
)
from loka.shared.application.context import current_context
from loka.shared.application.unit_of_work import UnitOfWork
from loka.shared.domain.errors import DomainError


class ConversationDesk:
    def __init__(self, uow: UnitOfWork, sessions: ConversationSessionRepository) -> None:
        self._uow = uow
        self._sessions = sessions

    async def settle(
        self,
        turn: PlugTurn,
        *,
        step: FlowStep,
        question_key: str | None,
        context: dict[str, Any] | None = None,
    ) -> None:
        """Open the task if this turn starts it, then record where it stands."""
        await self._move(
            turn.session,
            flow=turn.flow,
            fresh=turn.fresh,
            step=step,
            question_key=question_key,
            context=context or {},
            now=turn.now,
        )

    async def notify(
        self,
        user_id: uuid.UUID,
        text: str,
        *,
        now: datetime,
        steer: Steer | None = None,
        media_object_key: str | None = None,
    ) -> bool:
        """Write to another user, on the thread he last used. ``False`` when he has none.

        With ``media_object_key`` the stored image goes out, ``text`` as its caption.
        """
        session = await self._sessions.latest_by_user(user_id)
        if session is None:
            return False
        if steer is not None:
            await self._move(
                session,
                flow=steer.flow,
                fresh=session.flow is not steer.flow or not session.is_active,
                step=steer.step,
                question_key=steer.question_key,
                context=steer.context,
                now=now,
            )
        self._uow.events.stage(
            [
                WhatsAppMessageSendRequested(
                    aggregate_type="ConversationSession",
                    aggregate_id=session.id,
                    aggregate_version=session.revision,
                    occurred_at=now,
                    session_id=session.id,
                    recipient_phone=session.phone.e164,
                    reply_kind="QUESTION" if steer and steer.question_key else "ACK",
                    question_key=steer.question_key if steer else None,
                    text=text,
                    media_object_key=media_object_key,
                    intent="NOTIFY",
                    language=session.language,
                )
            ],
            correlation_id=current_context().get("correlation_id") or "unknown",
        )
        return True

    async def notify_phone(self, phone: str, text: str, *, now: datetime) -> bool:
        """Write to a number that is not a user, such as an agency's, on the
        thread it last used with Kora. ``False`` when it never wrote."""
        try:
            number = PhoneNumber.normalize(phone)
        except DomainError:
            return False
        session = await self._sessions.latest_by_phone(number)
        if session is None:
            return False
        self._stage(session, text, now=now, question_key=None)
        return True

    def _stage(
        self, session: ConversationSession, text: str, *, now: datetime, question_key: str | None
    ) -> None:
        self._uow.events.stage(
            [
                WhatsAppMessageSendRequested(
                    aggregate_type="ConversationSession",
                    aggregate_id=session.id,
                    aggregate_version=session.revision,
                    occurred_at=now,
                    session_id=session.id,
                    recipient_phone=session.phone.e164,
                    reply_kind="QUESTION" if question_key else "ACK",
                    question_key=question_key,
                    text=text,
                    intent="NOTIFY",
                    language=session.language,
                )
            ],
            correlation_id=current_context().get("correlation_id") or "unknown",
        )

    async def _move(
        self,
        session: ConversationSession,
        *,
        flow: Any,
        fresh: bool,
        step: FlowStep,
        question_key: str | None,
        context: dict[str, Any],
        now: datetime,
    ) -> None:
        observed = session.revision
        if fresh or session.flow is not flow or not session.is_active:
            session.start_flow(flow, now=now, first_step=step)
        else:
            session.transition(step, now=now, expected_revision=session.revision)
        session.merge_context(context, now=now)
        session.record_pending_question(question_key)
        await self._sessions.save(session, expected_revision=observed)
