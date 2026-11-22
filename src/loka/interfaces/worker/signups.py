"""Every new number on idia, told to the team as it arrives.

Called by the privacy desk with the welcome, which a number gets once, on its
very first message. The team's phones (WHATSAPP__ALERT_PHONES) read who wrote
and how many accounts idia has now. A team phone writing for the first time
is not announced to itself.
"""

from __future__ import annotations

from datetime import datetime

import phonenumbers
from sqlalchemy import func, select

from loka.bounded_contexts.auth.infrastructure.persistence.models import UserRow
from loka.bounded_contexts.conversation.domain.entities.conversation_session import (
    ConversationSession,
)
from loka.bounded_contexts.conversation.domain.events.message_events import (
    WhatsAppMessageSendRequested,
)
from loka.shared.infrastructure.db.unit_of_work import SqlAlchemyUnitOfWork


def _digits(value: str) -> str:
    return "".join(ch for ch in value if ch.isdigit())


def _readable(e164: str) -> str:
    try:
        number = phonenumbers.parse(e164, None)
    except phonenumbers.NumberParseException:
        return e164
    return phonenumbers.format_number(number, phonenumbers.PhoneNumberFormat.INTERNATIONAL)


def announcement(*, phone: str, name: str | None, total: int) -> str:
    who = _readable(phone) + (f" · « {name} »" if name else "")
    accounts = "1 inscrit" if total == 1 else f"{total} inscrits"
    return f"🆕 Nouvel utilisateur idia\n{who}\nTotal : {accounts}"


async def announce(
    uow: SqlAlchemyUnitOfWork, session: ConversationSession, *, alert_phones: str, now: datetime
) -> None:
    team = [p.strip() for p in alert_phones.split(",") if p.strip()]
    newcomer = _digits(session.phone.e164)
    recipients = [p for p in team if _digits(p) != newcomer]
    if not recipients:
        return
    total = int(await uow.session.scalar(select(func.count()).select_from(UserRow)) or 0)
    name = await uow.session.scalar(
        select(UserRow.display_name).where(UserRow.id == session.user_id)
    )
    text = announcement(phone=session.phone.e164, name=name, total=total)
    uow.events.stage(
        [
            WhatsAppMessageSendRequested(
                aggregate_type="ConversationSession",
                aggregate_id=session.id,
                aggregate_version=session.revision,
                occurred_at=now,
                recipient_phone=phone,
                text=text,
                intent="SIGNUP_ALERT",
                language="fr",
            )
            for phone in recipients
        ],
        correlation_id=f"signup-{session.id}",
    )
