"""10 % of a referee's settled purchase, credited to whoever brought them;
and every buyer, once per purchase, invited to share their own link.

A sweep, like the payment ones: it reads what was settled and never touches
how anyone pays. Each purchase is credited once (its source is the key).
"""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime

from loka.bounded_contexts.conversation.application.plugs.desk import ConversationDesk
from loka.bounded_contexts.conversation.infrastructure.persistence.conversation_session_repository import (  # noqa: E501
    SqlAlchemyConversationSessionRepository,
)
from loka.bounded_contexts.referral import texts
from loka.bounded_contexts.referral.domain.referral import CONTACT_THRESHOLD_XAF, link
from loka.bounded_contexts.referral.infrastructure.store import SqlAlchemyReferralStore
from loka.shared.infrastructure.composition import unit_of_work
from loka.shared.infrastructure.config.settings import Settings
from loka.shared.infrastructure.db.engine import Database
from loka.shared.infrastructure.logging import get_logger

_logger = get_logger(__name__)
SWEEP_INTERVAL_SECONDS = 30.0


async def credit_once(database: Database, settings: Settings) -> None:
    now = datetime.now(UTC)
    admins = [p.strip() for p in settings.whatsapp.alert_phones.split(",") if p.strip()]
    async with unit_of_work(database) as uow:
        store = SqlAlchemyReferralStore(uow.session)
        sessions = SqlAlchemyConversationSessionRepository(uow.session)
        desk = ConversationDesk(uow, sessions)
        for credit in await store.credit_new_purchases(now=now):
            _, earned = await store.standing(credit.referrer_user_id)
            await desk.notify(
                credit.referrer_user_id, texts.credited(credit.commission_xaf, earned), now=now
            )
            if earned - credit.commission_xaf < CONTACT_THRESHOLD_XAF <= earned:
                latest = await sessions.latest_by_user(credit.referrer_user_id)
                who = latest.phone.e164 if latest else str(credit.referrer_user_id)
                for admin in admins:
                    await desk.notify_phone(admin, texts.threshold_reached(who, earned), now=now)
            _logger.info("referral_credited", amount=credit.commission_xaf)
        # After a purchase, the buyer is invited to share their link. A sweep
        # later than the one that delivered what was bought, so it comes last.
        for buyer in await store.buyers_to_invite(now=now):
            url = link(await store.code_of(buyer, now=now), settings.whatsapp.public_phone)
            await desk.notify(buyer, texts.after_purchase(url), now=now, delay_seconds=5)
        await uow.commit()


async def sweep_forever(
    database: Database, settings: Settings, *, interval: float = SWEEP_INTERVAL_SECONDS
) -> None:
    while True:
        try:
            await credit_once(database, settings)
        except asyncio.CancelledError:
            raise
        except Exception as exc:  # pragma: no cover - resilience path
            _logger.exception("referral_sweep_failed", error=str(exc))
        await asyncio.sleep(interval)
