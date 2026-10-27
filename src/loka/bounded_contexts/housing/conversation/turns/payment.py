"""The tenant's answer to the 1 000 FCFA offer.

Platform text and one rule, never the model: "oui" must mean pay with the
number shown, a number must mean that number, and nothing else may start a
withdrawal. A misread charges the wrong number, or charges twice.
"""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any

from loka.bounded_contexts.conversation.application.ports import (
    UnlockPayments,
    UnlockRequestOutcome,
)
from loka.bounded_contexts.conversation.application.use_cases.analyse_conversation_message.turn import (  # noqa: E501
    Settled,
)
from loka.bounded_contexts.conversation.domain.entities.conversation_session import (
    ConversationSession,
    FlowStep,
)
from loka.bounded_contexts.housing.conversation.texts.match_proposals import (
    SHOWN_LISTINGS_KEY,
)
from loka.bounded_contexts.housing.conversation.texts.unlock_copy import (
    PAYMENT_QUESTION_KEY,
    UNLOCK_KEY,
    PaymentAnswer,
    read_payment_answer,
    unlock_text,
)

PAYMENT_STEPS = frozenset({FlowStep.CONFIRM_PAYMENT, FlowStep.AWAITING_PAYMENT})


def _as_uuid(value: Any) -> uuid.UUID | None:
    """A stored id read back out of a JSON column, or ``None`` if unusable.

    A malformed booking must leave the tenant with a question, never start a
    withdrawal against a garbage id.
    """
    if not isinstance(value, str):
        return None
    try:
        return uuid.UUID(value)
    except (ValueError, AttributeError, TypeError):
        return None


def _back_to_his_list(session: ConversationSession) -> FlowStep:
    """Where a tenant goes when the listing he picked falls through.

    His results when he still has a list to choose from — otherwise waiting
    for a match — so a dead end never becomes a dead thread.
    """
    shown = session.context.get(SHOWN_LISTINGS_KEY)
    return (
        FlowStep.COLLECT_PROPERTY_CHOICE
        if isinstance(shown, list) and shown
        else FlowStep.AWAITING_MATCH
    )


class PaymentTurn:
    def __init__(self, payments: UnlockPayments | None) -> None:
        self._payments = payments

    async def settle(
        self, session: ConversationSession, *, text: str | None, intent: str, now: datetime
    ) -> Settled:
        """Only reached on the payment steps, and it always settles the turn."""
        language = session.language
        phone = session.phone.e164

        def settled(
            step: FlowStep, key: str | None, reply: str, *, clear: bool = False
        ) -> Settled:
            return Settled(
                next_step=step, question_key=key, reply=reply, intent=intent, clear_unlock=clear
            )

        if session.step is FlowStep.AWAITING_PAYMENT:
            # The withdrawal is on his phone; only the provider's callback moves
            # him on, never his word that he paid.
            return settled(
                FlowStep.AWAITING_PAYMENT,
                PAYMENT_QUESTION_KEY,
                unlock_text("waiting", language, phone=phone),
            )

        booking = session.context.get(UNLOCK_KEY)
        property_id = landlord_id = None
        if isinstance(booking, dict):
            property_id = _as_uuid(booking.get("property_id"))
            landlord_id = _as_uuid(booking.get("landlord_id"))
        if property_id is None or landlord_id is None or session.user_id is None:
            # Nothing to unlock: the booking is gone or never existed. Re-asking
            # is honest; inventing a listing to charge for is not.
            return settled(
                FlowStep.CONFIRM_PAYMENT,
                PAYMENT_QUESTION_KEY,
                unlock_text("not_started", language, phone=phone),
            )

        answer, other_number = read_payment_answer(text or "")
        if answer is PaymentAnswer.CANCEL:
            # Nothing is charged. He is sent back to his results when there are
            # any left, and to waiting otherwise — never left with no way out.
            return settled(
                _back_to_his_list(session),
                None,
                unlock_text("cancelled", language, phone=phone),
                clear=True,
            )
        if answer is PaymentAnswer.UNCLEAR or self._payments is None:
            return settled(
                FlowStep.CONFIRM_PAYMENT,
                PAYMENT_QUESTION_KEY,
                unlock_text(
                    "unclear" if answer is PaymentAnswer.UNCLEAR else "not_started",
                    language,
                    phone=phone,
                ),
            )

        # ``read_payment_answer`` only returns a number on OTHER_NUMBER, but the
        # type is optional; falling back to the thread's own number keeps the
        # request well-formed rather than crashing the turn.
        payer_phone = (
            other_number if answer is PaymentAnswer.OTHER_NUMBER and other_number else phone
        )
        outcome = await self._payments.request(
            tenant_id=session.user_id,
            property_id=property_id,
            landlord_id=landlord_id,
            payer_phone=payer_phone,
            now=now,
        )
        if outcome is UnlockRequestOutcome.REQUESTED:
            return settled(
                FlowStep.AWAITING_PAYMENT,
                PAYMENT_QUESTION_KEY,
                unlock_text("requested", language, phone=payer_phone),
            )
        if outcome is UnlockRequestOutcome.ALREADY_UNLOCKED:
            return settled(
                FlowStep.DONE, None, unlock_text("already", language, phone=phone), clear=True
            )
        if outcome is UnlockRequestOutcome.NOT_CONFIRMED:
            # The confirmation was stale — the listing went between the offer
            # and his yes. Nothing is charged, and he is put back on his list.
            return settled(
                _back_to_his_list(session),
                None,
                unlock_text("not_confirmed", language, phone=phone),
                clear=True,
            )
        # FAILED: nothing was recorded as owed, so another attempt is honest.
        return settled(
            FlowStep.CONFIRM_PAYMENT,
            PAYMENT_QUESTION_KEY,
            unlock_text("failed", language, phone=payer_phone),
        )
