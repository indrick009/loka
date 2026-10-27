"""The landlord's answer to "le logement est encore disponible ?" and to an offer."""

from __future__ import annotations

from datetime import datetime

from loka.bounded_contexts.conversation.application.ports import AvailabilityAnswers
from loka.bounded_contexts.conversation.application.services.conversation_copy import (
    availability_answer_text,
)
from loka.bounded_contexts.conversation.application.services.negotiation_copy import (
    read_offer_reply,
)
from loka.bounded_contexts.conversation.application.use_cases.analyse_conversation_message.housing.keys import (  # noqa: E501
    is_availability_answer,
)
from loka.bounded_contexts.conversation.application.use_cases.analyse_conversation_message.turn import (  # noqa: E501
    Settled,
)
from loka.bounded_contexts.conversation.domain.entities.conversation_session import (
    ConversationSession,
)


class LandlordAvailability:
    def __init__(self, answers: AvailabilityAnswers | None) -> None:
        self._answers = answers

    async def record(
        self, session: ConversationSession, *, intent: str, now: datetime
    ) -> str | None:
        """Apply the answer to the listing it was about.

        These intents were recognised and then discarded: a landlord could
        write "c'est déjà loué" and the lodging stayed in every tenant's search
        results. Nothing else in the system can take a listing out — Kora never
        infers availability — so this answer is the only thing that does.

        ``None`` when the turn was not one of these answers, or when the
        landlord was never asked: the reply then comes from wherever it usually
        would.
        """
        if self._answers is None or session.user_id is None:
            return None
        if not is_availability_answer(intent):
            return None
        result = await self._answers.record_answer(
            landlord_id=session.user_id, answer=intent, now=now
        )
        if result is None:
            return None
        return availability_answer_text(
            session.language, lodgings_left=result.lodgings_left, delisted=result.delisted
        )

    async def settle_offer(
        self, session: ConversationSession, *, text: str | None, now: datetime
    ) -> Settled | None:
        """His yes, no or price to a tenant's offer (§8), read by a rule.

        A "oui" commits him to a price, and a number is a price passed on, so
        neither is left to the model. ``None`` for anything else ("c'est
        pris"), which the ordinary availability reading already knows.
        """
        reply = read_offer_reply(text)
        if reply is None or self._answers is None or session.user_id is None:
            return None
        result = await self._answers.record_answer(
            landlord_id=session.user_id,
            answer="AVAILABILITY_STILL_AVAILABLE",
            now=now,
            offer_reply=reply,
        )
        if result is None:
            return None
        return Settled(
            next_step=session.step,
            question_key=None,
            reply=availability_answer_text(
                session.language, lodgings_left=result.lodgings_left, delisted=result.delisted
            ),
            intent="AVAILABILITY_STILL_AVAILABLE",
        )
