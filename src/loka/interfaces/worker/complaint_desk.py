"""Before a message is read: a complaint, from anyone, goes to a person.

Run by the analysis worker after the privacy desk, before any model (words in
``services/complaints``):

1. « faux bailleur », « arnaque », « je veux faire une réclamation »… opens a
   numbered report. A message that already tells the story is filed at once;
   a bare « réclamation » is asked « raconte-moi ce qui s'est passé ».
2. The next message, text or screenshot, completes it: the report is filed,
   the user gets its reference and « je te reviens très vite », and every
   number in ``WHATSAPP__ALERT_PHONES`` receives it with the last exchanges.
3. For half an hour after, a screenshot is added to the report and forwarded.

Accepted whether or not idia introduced the two people: the report starts
unverified, and a person decides. A desk that fails lets the message through:
a user must never go unanswered because the report could not be written.
"""

from __future__ import annotations

import secrets
import uuid
from datetime import UTC, datetime

from redis.asyncio import Redis
from redis.exceptions import RedisError
from sqlalchemy import select

from loka.bounded_contexts.conversation.application.services import complaints
from loka.bounded_contexts.conversation.domain.entities.conversation_session import (
    ConversationSession,
)
from loka.bounded_contexts.conversation.domain.events.message_events import (
    WhatsAppMessageSendRequested,
)
from loka.bounded_contexts.conversation.infrastructure.persistence.conversation_session_repository import (  # noqa: E501
    SqlAlchemyConversationSessionRepository,
)
from loka.bounded_contexts.conversation.infrastructure.persistence.inbound_message_repository import (  # noqa: E501
    SqlAlchemyInboundMessageRepository,
)
from loka.bounded_contexts.conversation.infrastructure.persistence.repositories import (
    SqlAlchemyConversationExchanges,
)
from loka.bounded_contexts.reviews_complaints.infrastructure.persistence.models import (
    ComplaintReportRow,
)
from loka.shared.infrastructure.composition import unit_of_work
from loka.shared.infrastructure.config.settings import Settings
from loka.shared.infrastructure.conversation_log import journal
from loka.shared.infrastructure.db.engine import Database
from loka.shared.infrastructure.db.unit_of_work import SqlAlchemyUnitOfWork as UnitOfWork
from loka.shared.infrastructure.logging import get_logger

_logger = get_logger(__name__)

_PENDING = "complaint:{}"
_WINDOW_SECONDS = 1800
_ASKING = "asking"
_FILED = "filed"
_THREAD_LINES = 8


class ComplaintDesk:
    def __init__(self, database: Database, redis: Redis, settings: Settings) -> None:
        self._database = database
        self._redis = redis
        self._alert_phones = [
            p.strip() for p in settings.whatsapp.alert_phones.split(",") if p.strip()
        ]

    async def handle(self, message_id: uuid.UUID, session_id: uuid.UUID) -> bool:
        """Whether the message was fully answered here and needs no analysis."""
        try:
            return await self._handle(message_id, session_id)
        except Exception as exc:  # see the module docstring
            _logger.warning("complaint_desk_failed", error=type(exc).__name__)
            return False

    async def _handle(self, message_id: uuid.UUID, session_id: uuid.UUID) -> bool:
        now = datetime.now(UTC)
        pending = await self._pending(session_id)
        async with unit_of_work(self._database) as uow:
            inbound = SqlAlchemyInboundMessageRepository(uow.session)
            message = await inbound.get_by_id(message_id)
            session = await SqlAlchemyConversationSessionRepository(uow.session).get_by_id(
                session_id
            )
            if message is None or session is None:
                return False
            text = (message.text or "").strip()
            media = message.media_object_key
            report = await self._report(uow, pending)

            if report is not None and pending.get("state") == _ASKING and (text or media):
                self._add(report, text, media, now)
                await self._file(uow, session, report, now)
            elif report is not None and pending.get("state") == _FILED and media:
                self._add(report, "", media, now)
                self._say(uow, session, complaints.screenshot_added(
                    session.language, reference=report.reference
                ), now)
                self._alert(uow, session, complaints.screenshot_alert(
                    reference=report.reference, phone=report.phone
                ), now, media=media)
            elif text and complaints.is_complaint(text):
                report = self._open(uow, session, text, media, now)
                if complaints.is_detailed(text) or media:
                    await self._file(uow, session, report, now)
                else:
                    await self._set_pending(session_id, report, _ASKING)
                    self._say(uow, session, complaints.ask_details(session.language), now)
            else:
                return False
            await inbound.mark_processed(message_id)
            await uow.commit()
            journal("complaint", report.phone, reference=report.reference)
            return True

    def _open(
        self,
        uow: UnitOfWork,
        session: ConversationSession,
        text: str,
        media: str | None,
        now: datetime,
    ) -> ComplaintReportRow:
        report = ComplaintReportRow(
            id=uuid.uuid4(),
            reference=f"R-{secrets.token_hex(3).upper()}",
            phone=session.phone.e164,
            session_id=session.id,
            account=text,
            media_keys=[media] if media else [],
            verified=False,
            status="OPEN",
            created_at=now,
            updated_at=now,
        )
        uow.session.add(report)
        return report

    @staticmethod
    def _add(report: ComplaintReportRow, text: str, media: str | None, now: datetime) -> None:
        if text:
            report.account = f"{report.account}\n{text}".strip()
        if media:
            report.media_keys = [*report.media_keys, media]
        report.updated_at = now

    async def _file(
        self,
        uow: UnitOfWork,
        session: ConversationSession,
        report: ComplaintReportRow,
        now: datetime,
    ) -> None:
        """The reference to the user, the report to the team."""
        await self._set_pending(session.id, report, _FILED)
        self._say(uow, session, complaints.recorded(
            session.language, reference=report.reference
        ), now)
        thread = await SqlAlchemyConversationExchanges(uow.session).recent(
            session.id, limit=_THREAD_LINES
        )
        alert = complaints.alert(
            reference=report.reference,
            phone=report.phone,
            account=report.account,
            thread=thread,
            screenshots=len(report.media_keys),
        )
        self._alert(uow, session, alert, now)
        for key in report.media_keys:
            self._alert(uow, session, complaints.screenshot_alert(
                reference=report.reference, phone=report.phone
            ), now, media=key)
        _logger.warning("complaint_filed", reference=report.reference)

    async def _report(
        self, uow: UnitOfWork, pending: dict[str, str]
    ) -> ComplaintReportRow | None:
        if not pending.get("id"):
            return None
        report: ComplaintReportRow | None = await uow.session.scalar(
            select(ComplaintReportRow).where(ComplaintReportRow.id == uuid.UUID(pending["id"]))
        )
        return report

    @staticmethod
    def _say(uow: UnitOfWork, session: ConversationSession, text: str, now: datetime) -> None:
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
                    language=session.language,
                )
            ],
            correlation_id=f"complaint-{session.id}",
        )

    def _alert(
        self,
        uow: UnitOfWork,
        session: ConversationSession,
        text: str,
        now: datetime,
        *,
        media: str | None = None,
    ) -> None:
        """To the team; no session, so nothing of it lands in the user's thread."""
        uow.events.stage(
            [
                WhatsAppMessageSendRequested(
                    aggregate_type="ConversationSession",
                    aggregate_id=session.id,
                    aggregate_version=session.revision,
                    occurred_at=now,
                    recipient_phone=phone,
                    text=text,
                    media_object_key=media,
                    intent="COMPLAINT_ALERT",
                    language="fr",
                )
                for phone in self._alert_phones
            ],
            correlation_id=f"complaint-{session.id}",
        )

    async def _pending(self, session_id: uuid.UUID) -> dict[str, str]:
        try:
            raw = await self._redis.hgetall(_PENDING.format(session_id))
            return {str(k): str(v) for k, v in raw.items()}
        except RedisError:
            return {}

    async def _set_pending(
        self, session_id: uuid.UUID, report: ComplaintReportRow, state: str
    ) -> None:
        key = _PENDING.format(session_id)
        try:
            await self._redis.hset(
                key, mapping={"id": str(report.id), "state": state}
            )
            await self._redis.expire(key, _WINDOW_SECONDS)
        except RedisError as exc:
            _logger.warning("complaint_pending_unavailable", error=type(exc).__name__)
