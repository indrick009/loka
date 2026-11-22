"""Before a message is read: what goes to the team, and what comes from it.

Run by the analysis worker after the privacy desk, before any model:

- **From the admin.** A number of ``WHATSAPP__ALERT_PHONES`` writing
  « I-7F3A2C le meublé n'existe pas encore, propose un studio non meublé »
  adds that instruction to incident I-7F3A2C. Whoever fixes the conversation
  reads it with ``holds list`` and releases the user (``holds release``).
- **To the team.** A request close to what idia does but not offered yet
  (``services/unsupported``) is not answered by a model: the user reads that
  idia checks with the team, the conversation waits, the admin is alerted
  with the incident number (``stuck_guard``, through the outbound worker).

A desk that fails lets the message through: never a silence because of it.
"""

from __future__ import annotations

import re
import uuid
from datetime import UTC, datetime

from redis.asyncio import Redis

from loka.bounded_contexts.conversation.application.services.privacy import fold
from loka.bounded_contexts.conversation.application.services.unsupported import (
    unsupported_topic,
)
from loka.bounded_contexts.conversation.domain.entities.conversation_session import (
    ConversationSession,
)
from loka.bounded_contexts.conversation.domain.events.message_events import (
    WhatsAppMessageSendRequested,
)
from loka.bounded_contexts.conversation.infrastructure.adapters.redis_holds import (
    RedisConversationHolds,
)
from loka.bounded_contexts.conversation.infrastructure.persistence.conversation_session_repository import (  # noqa: E501
    SqlAlchemyConversationSessionRepository,
)
from loka.bounded_contexts.conversation.infrastructure.persistence.inbound_message_repository import (  # noqa: E501
    SqlAlchemyInboundMessageRepository,
)
from loka.interfaces.worker import signups
from loka.interfaces.worker.stuck_guard import ESCALATE
from loka.shared.application.unit_of_work import UnitOfWork
from loka.shared.infrastructure.composition import unit_of_work
from loka.shared.infrastructure.config.settings import Settings
from loka.shared.infrastructure.conversation_log import journal
from loka.shared.infrastructure.db.engine import Database
from loka.shared.infrastructure.logging import get_logger

_logger = get_logger(__name__)

_NOTE = re.compile(r"^\s*(I-[0-9A-F]{6})\b[\s:,-]*(.*)$", re.IGNORECASE | re.DOTALL)
# An admin asking for the numbers: answered here, never handed to the team —
# handed over, « combien d'utilisateurs ? » held the admin's own conversation
# (22 Nov 2026, 18:41 UTC).
_FIGURES_ASKED = re.compile(
    r"\b(stats?|statistiques?|chiffres?)\b"
    r"|\b(combien|nombre)\b.*\b(utilisateurs?|inscrits?|users?|clients?|membres?)\b"
)


def _digits(phone: str) -> str:
    return "".join(c for c in phone if c.isdigit())


class TeamDesk:
    def __init__(self, database: Database, redis: Redis, settings: Settings) -> None:
        self._database = database
        self._holds = RedisConversationHolds(redis)
        self._admins = {
            _digits(p) for p in settings.whatsapp.alert_phones.split(",") if p.strip()
        }

    async def note(self, message_id: uuid.UUID, session_id: uuid.UUID) -> bool:
        """An admin's instruction; read even when the admin's own thread is held."""
        return await self._safely(message_id, session_id, notes=True)

    async def escalate(self, message_id: uuid.UUID, session_id: uuid.UUID) -> bool:
        """A request idia does not offer yet, handed to a person."""
        return await self._safely(message_id, session_id, notes=False)

    async def _safely(self, message_id: uuid.UUID, session_id: uuid.UUID, *, notes: bool) -> bool:
        try:
            return await self._handle(message_id, session_id, notes=notes)
        except Exception as exc:  # see the module docstring
            _logger.warning("team_desk_failed", error=type(exc).__name__)
            return False

    async def _handle(self, message_id: uuid.UUID, session_id: uuid.UUID, *, notes: bool) -> bool:
        now = datetime.now(UTC)
        async with unit_of_work(self._database) as uow:
            inbound = SqlAlchemyInboundMessageRepository(uow.session)
            message = await inbound.get_by_id(message_id)
            session = await SqlAlchemyConversationSessionRepository(uow.session).get_by_id(
                session_id
            )
            if message is None or session is None:
                return False
            text = (message.text or "").strip()
            phone = session.phone.e164
            admin = notes and _digits(phone) in self._admins
            if admin and _FIGURES_ASKED.search(fold(text)):
                answer = await signups.figures(uow.session)
                self._say(uow, session, answer, now, intent=None)
                await inbound.mark_processed(message_id)
                await uow.commit()
                journal("team", phone, figures=True)
                return True
            note = _NOTE.match(text) if admin else None
            if notes and note is None:
                return False
            if note is not None:
                answer = await self._note(note.group(1).upper(), note.group(2).strip())
            else:
                topic = unsupported_topic(text) if text else None
                if topic is None:
                    return False
                answer = f"{topic} — « {text} »"
            self._say(uow, session, answer, now, intent=None if note is not None else ESCALATE)
            await inbound.mark_processed(message_id)
            await uow.commit()
            journal("team", phone, note=note is not None, text=answer)
            return True

    @staticmethod
    def _say(
        uow: UnitOfWork,
        session: ConversationSession,
        text: str,
        now: datetime,
        *,
        intent: str | None,
    ) -> None:
        uow.events.stage(
            [
                WhatsAppMessageSendRequested(
                    aggregate_type="ConversationSession",
                    aggregate_id=session.id,
                    aggregate_version=session.revision,
                    occurred_at=now,
                    actor_id=session.user_id,
                    session_id=session.id,
                    recipient_phone=session.phone.e164,
                    text=text,
                    intent=intent,
                    language=session.language,
                )
            ],
            correlation_id=f"team-{session.id}",
        )

    async def _note(self, incident: str, note: str) -> str:
        hold = await self._holds.by_incident(incident)
        if hold is None:
            return f"Incident {incident} introuvable ou déjà réglé."
        if not note:
            return f"Incident {incident} : écris la consigne après le numéro."
        await self._holds.add_note(hold, note)
        _logger.warning("incident_note_added", incident=incident)
        return f"Consigne notée pour {incident} ✅ ({hold.phone} attend toujours)."
