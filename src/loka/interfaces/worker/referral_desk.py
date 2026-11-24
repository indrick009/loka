"""Before a message is read: referral links, asked for and arriving.

- « parrainage », « mon lien », « mes gains »… gets the member's link and
  credit when they have paid once, or how to open it when they have not.
- The prefilled message of a link (« … je viens de la part de IDIA-7K3F2 »)
  on a number's very first message records who brought it (called by the
  privacy desk, which alone knows it is the first).

A desk that fails lets the message through: never a silence because of it.
"""

from __future__ import annotations

import re
import uuid
from datetime import UTC, datetime

from loka.bounded_contexts.conversation.application.plugs.desk import ConversationDesk
from loka.bounded_contexts.conversation.application.services.privacy import fold
from loka.bounded_contexts.conversation.domain.entities.conversation_session import (
    ConversationSession,
)
from loka.bounded_contexts.conversation.infrastructure.persistence.conversation_session_repository import (  # noqa: E501
    SqlAlchemyConversationSessionRepository,
)
from loka.bounded_contexts.conversation.infrastructure.persistence.inbound_message_repository import (  # noqa: E501
    SqlAlchemyInboundMessageRepository,
)
from loka.bounded_contexts.referral import texts
from loka.bounded_contexts.referral.domain.referral import code_in, link
from loka.bounded_contexts.referral.infrastructure.store import SqlAlchemyReferralStore
from loka.shared.infrastructure.composition import unit_of_work
from loka.shared.infrastructure.config.settings import Settings
from loka.shared.infrastructure.conversation_log import journal
from loka.shared.infrastructure.db.engine import Database
from loka.shared.infrastructure.db.unit_of_work import SqlAlchemyUnitOfWork
from loka.shared.infrastructure.logging import get_logger

_logger = get_logger(__name__)

_ASKED = re.compile(
    r"\b(parrain\w*|filleul\w*|referral|mon lien|mon code|mes gains|mes commissions)\b"
)


async def welcome_referee(
    uow: SqlAlchemyUnitOfWork, session: ConversationSession, text: str, *, now: datetime
) -> bool:
    """On a first message: record the referrer named by its code. Whether one was."""
    code = code_in(text)
    if code is None or session.user_id is None:
        return False
    referrer = await SqlAlchemyReferralStore(uow.session).attribute(
        referee=session.user_id, code=code, now=now
    )
    if referrer is None:
        return False
    sessions = SqlAlchemyConversationSessionRepository(uow.session)
    await ConversationDesk(uow, sessions).notify(referrer, texts.newcomer(), now=now)
    journal("referral", session.phone.e164, referred_by=code)
    return True


class ReferralDesk:
    def __init__(self, database: Database, settings: Settings) -> None:
        self._database = database
        self._idia_phone = settings.whatsapp.public_phone

    async def handle(self, message_id: uuid.UUID, session_id: uuid.UUID) -> bool:
        """Whether the message was a referral request, answered here."""
        try:
            return await self._handle(message_id, session_id)
        except Exception as exc:  # see the module docstring
            _logger.warning("referral_desk_failed", error=type(exc).__name__)
            return False

    async def _handle(self, message_id: uuid.UUID, session_id: uuid.UUID) -> bool:
        now = datetime.now(UTC)
        async with unit_of_work(self._database) as uow:
            inbound = SqlAlchemyInboundMessageRepository(uow.session)
            message = await inbound.get_by_id(message_id)
            sessions = SqlAlchemyConversationSessionRepository(uow.session)
            session = await sessions.get_by_id(session_id)
            if message is None or session is None or session.user_id is None:
                return False
            if not _ASKED.search(fold(message.text or "")):
                return False
            store = SqlAlchemyReferralStore(uow.session)
            desk = ConversationDesk(uow, sessions)
            if not await store.has_bought(session.user_id):
                await desk.notify(session.user_id, texts.not_yet(), now=now)
            else:
                url = link(await store.code_of(session.user_id, now=now), self._idia_phone)
                referees, earned = await store.standing(session.user_id)
                await desk.notify(session.user_id, texts.your_link(url, referees, earned), now=now)
                await desk.notify(
                    session.user_id, texts.to_forward(url), now=now, delay_seconds=2
                )
            await inbound.mark_processed(message_id)
            await uow.commit()
            journal("referral", session.phone.e164, asked=True)
            return True
