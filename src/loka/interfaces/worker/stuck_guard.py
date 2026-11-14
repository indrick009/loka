"""Stop a conversation that goes round in circles, and call a person.

Asked by the outbound worker on the final sentence of every reply to a turn.
When the reply shows the thread is stuck (see ``services/stuck``), it is not
sent: the user reads that an answer is coming, the conversation is held — its
next messages are stored and left unanswered — and the people named in
``WHATSAPP__ALERT_PHONES`` are told, with the lines needed to see why.

Released with ``python -m loka.interfaces.cli.holds release <phone>`` once the
defect is fixed. WhatsApp users do not expect an instant answer; they do
notice the same sentence three times.

A guard that cannot read the thread lets the reply through: silence because
the guard broke would be the failure it exists to prevent.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime
from typing import Any

from loka.bounded_contexts.conversation.application.ports import Exchange
from loka.bounded_contexts.conversation.application.services.stuck import stuck_signal
from loka.bounded_contexts.conversation.infrastructure.adapters.redis_holds import (
    Hold,
    RedisConversationHolds,
)
from loka.bounded_contexts.conversation.infrastructure.persistence.repositories import (
    SqlAlchemyConversationExchanges,
)
from loka.shared.infrastructure.composition import unit_of_work
from loka.shared.infrastructure.conversation_log import journal
from loka.shared.infrastructure.db.engine import Database
from loka.shared.infrastructure.logging import get_logger

_logger = get_logger(__name__)

# Enough of the thread to see a sentence come back after a different message.
THREAD_LINES = 10
_ALERT_LINES = 6

_HOLDING = {
    "fr": "Je vérifie un point pour vous répondre correctement, je vous reviens très vite 🙏",
    "en": "Let me check something so I answer you properly, I'll get back to you shortly 🙏",
}


def holding_text(language: str | None) -> str:
    return _HOLDING["en" if (language or "").casefold().startswith("en") else "fr"]


def alert_text(hold: Hold, thread: list[Exchange]) -> str:
    lines = "\n".join(
        f"{'👤' if line.speaker == 'user' else '🤖'} {line.text}" for line in thread[-_ALERT_LINES:]
    )
    return (
        f"⚠️ Conversation en pause : {hold.phone}\n"
        f"Signal : {hold.signal}\n\n"
        f"{lines}\n\n"
        f"Réponse retenue :\n{hold.withheld}\n\n"
        f"Reprendre : holds release {hold.phone}"
    )


class StuckGuard:
    def __init__(
        self,
        database: Database,
        holds: RedisConversationHolds,
        *,
        enabled: bool,
        alert_phones: list[str],
    ) -> None:
        self._database = database
        self._holds = holds
        self._enabled = enabled
        self._alert_phones = alert_phones

    @staticmethod
    def watches(metadata: dict[str, Any]) -> bool:
        """Replies to a turn only; a photo acknowledgement repeats by nature."""
        return bool(metadata.get("turn_reply")) and not (
            metadata.get("media_burst_of") or metadata.get("media_object_key")
        )

    async def held(self, session_id: uuid.UUID) -> bool:
        return self._enabled and await self._holds.get(session_id) is not None

    async def check(
        self, text: str, metadata: dict[str, Any], session_id: uuid.UUID
    ) -> tuple[str, list[Exchange]] | None:
        """The signal and the thread it was read on, or ``None``: send the reply."""
        if not self._enabled:
            return None
        misread = await self._holds.misread_run(
            session_id, misread=metadata.get("reply_kind") == "CLARIFY"
        )
        try:
            async with unit_of_work(self._database) as uow:
                thread = await SqlAlchemyConversationExchanges(uow.session).recent(
                    session_id, limit=THREAD_LINES
                )
        except Exception as exc:  # see the module docstring
            _logger.warning("stuck_guard_unreadable", error=type(exc).__name__)
            return None
        signal = stuck_signal(text, thread, misread_run=misread)
        return (signal, thread) if signal is not None else None

    async def hold(
        self,
        *,
        session_id: uuid.UUID,
        recipient: str,
        signal: str,
        language: str | None,
        withheld: str,
        thread: list[Exchange],
    ) -> list[tuple[str, str]]:
        """Hold the conversation; the alerts to send, one per person to tell."""
        hold = Hold(
            session_id=str(session_id),
            phone=recipient,
            signal=signal,
            language=language or "fr",
            since=datetime.now(UTC).isoformat(timespec="seconds"),
            withheld=withheld,
        )
        await self._holds.put(hold)
        journal("incident", recipient, signal=signal, withheld=withheld, session_id=str(session_id))
        _logger.error(
            "conversation_held", session_id=str(session_id), signal=signal, recipient=recipient
        )
        alert = alert_text(hold, thread)
        return [(phone, alert) for phone in self._alert_phones]
