"""Every new number on idia, told to the team as it arrives.

Called by the privacy desk with the welcome, which a number gets once, on its
very first message. The team's phones (WHATSAPP__ALERT_PHONES) read who wrote
and how many accounts idia has now. A team phone writing for the first time
is not announced to itself.
"""

from __future__ import annotations

from datetime import datetime

import phonenumbers
from sqlalchemy import func, select, text
from sqlalchemy.ext.asyncio import AsyncSession

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
    message = announcement(phone=session.phone.e164, name=name, total=total)
    uow.events.stage(
        [
            WhatsAppMessageSendRequested(
                aggregate_type="ConversationSession",
                aggregate_id=session.id,
                aggregate_version=session.revision,
                occurred_at=now,
                recipient_phone=phone,
                text=message,
                intent="SIGNUP_ALERT",
                language="fr",
            )
            for phone in recipients
        ],
        correlation_id=f"signup-{session.id}",
    )


# What an admin reads when asking « combien d'utilisateurs ? » or « stats ».
_FIGURES = (
    ("Inscrits", "select count(*) from users"),
    (
        "dont nouveaux en 24 h",
        "select count(*) from users where created_at > now() - interval '24 hours'",
    ),
    ("Logements publiés", "select count(*) from properties where status = 'AVAILABLE'"),
    ("Restaurants", "select count(*) from food_restaurants"),
    ("Profils rencontre", "select count(*) from dating_profiles"),
    ("Objets trouvés déclarés", "select count(*) from found_declarations"),
    (
        "Paiements logement réussis",
        "select count(*) from service_fee_payments where status = 'SUCCEEDED'",
    ),
    ("Mises en relation payées", "select count(*) from contact_unlocks where status = 'PAID'"),
    ("Billets de car payés", "select count(*) from booking_reservations where status = 'PAID'"),
)


async def figures(session: AsyncSession) -> str:
    lines = ["📊 idia en chiffres"]
    for label, query in _FIGURES:
        count = int(await session.scalar(text(query)) or 0)
        lines.append(f"{label} : {count}")
    return "\n".join(lines)
