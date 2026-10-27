"""The tenant's answer to "cherchez-vous toujours ?", a month into his search."""

from __future__ import annotations

from datetime import datetime

from loka.bounded_contexts.conversation.application.ports import DemandFollowUp
from loka.bounded_contexts.conversation.application.services.demand_reminder import (
    reminder_text,
    still_searching,
)
from loka.bounded_contexts.conversation.application.use_cases.analyse_conversation_message.turn import (  # noqa: E501
    Settled,
)
from loka.bounded_contexts.conversation.domain.entities.conversation_session import (
    ConversationSession,
)


class SearchReminder:
    def __init__(self, follow_up: DemandFollowUp | None) -> None:
        self._follow_up = follow_up

    async def settle(
        self, session: ConversationSession, *, text: str | None, intent: str, now: datetime
    ) -> Settled | None:
        """Renew or end a month of proposals on a plain yes or no.

        Answered by a rule; what the rule cannot settle carries on (``None``).
        """
        answer = still_searching(text)
        if answer is None or self._follow_up is None or session.user_id is None:
            return None
        if answer:
            await self._follow_up.renew(tenant_id=session.user_id, now=now)
        else:
            await self._follow_up.stop(tenant_id=session.user_id, now=now)
        return Settled(
            next_step=session.step,
            question_key=None,
            reply=reminder_text("renewed" if answer else "stopped", session.language),
            intent=intent,
        )
