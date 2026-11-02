"""The sweep that hands over contacts once their 1 000 FCFA is validated.

Dating: each member of the match gets the other's WhatsApp number, or a member
who bought more questions gets them. Found: the owner gets the finder's, the
finder the owner's, with the handover rules.
"""

from __future__ import annotations

import asyncio
import uuid
from datetime import UTC, datetime

from loka.bounded_contexts.conversation.application.plugs.desk import ConversationDesk
from loka.bounded_contexts.conversation.domain.repositories.conversation_session_repository import (
    ConversationSessionRepository,
)
from loka.bounded_contexts.conversation.infrastructure.messaging_composition import (
    CONVERSATION_SESSION_REPOSITORY,
)
from loka.bounded_contexts.dating.domain.talk import MESSAGES_PER_PACK, PHOTOS_PER_PACK
from loka.bounded_contexts.dating.infrastructure.store import SqlAlchemyDatingStore
from loka.bounded_contexts.payment.domain.contact_unlock import DATING, DATING_PLUS, FAILED, PAID
from loka.bounded_contexts.payment.infrastructure.mobile_money import MobileMoneyPayments
from loka.bounded_contexts.payment.infrastructure.unlock_store import SqlAlchemyUnlockStore
from loka.shared.infrastructure.composition import payment_gateway, unit_of_work
from loka.shared.infrastructure.config.settings import Settings
from loka.shared.infrastructure.db.engine import Database
from loka.shared.infrastructure.logging import get_logger

_logger = get_logger(__name__)

SWEEP_INTERVAL_SECONDS = 10.0
HANDOVER = (
    "Pour la restitution : rencontrez-vous dans un lieu public ou au commissariat. "
    "Aucun argent n'est dû entre vous : signalez à Kora toute demande d'argent."
)


async def _phone(sessions: ConversationSessionRepository, user_id: uuid.UUID) -> str | None:
    session = await sessions.latest_by_user(user_id)
    return session.phone.e164 if session else None


async def settle_once(database: Database, settings: Settings) -> None:
    payments = MobileMoneyPayments(payment_gateway(settings))
    now = datetime.now(UTC)
    async with unit_of_work(database) as uow:
        store = SqlAlchemyUnlockStore(uow.session)
        sessions = uow.repository(CONVERSATION_SESSION_REPOSITORY)
        desk = ConversationDesk(uow, sessions)
        dating = SqlAlchemyDatingStore(uow.session)
        for unlock in await store.pending():
            status = await payments.status(unlock.payment_reference)
            if status in ("FAILED", "EXPIRED"):
                unlock.status = FAILED
                await store.save(unlock)
                await desk.notify(
                    unlock.payer_user_id,
                    "Le paiement n'a pas abouti. Réponds PAYER pour réessayer.",
                    now=now,
                )
                continue
            if status != "SUCCEEDED":
                continue
            unlock.status = PAID
            await store.save(unlock)
            if unlock.purpose == DATING_PLUS:
                payer = await dating.profile_of(unlock.payer_user_id)
                talk = await dating.talk(unlock.subject_id)
                if payer is not None and talk is not None:
                    talk.side(payer.profile_id).packs += 1
                    await dating.save_talk(talk)
                await desk.notify(
                    unlock.payer_user_id,
                    f"Paiement reçu : tu as {MESSAGES_PER_PACK} messages et "
                    f"{PHOTOS_PER_PACK} photos de plus. Écris ta question, je la transmets.",
                    now=now,
                )
                continue
            payer_phone = await _phone(sessions, unlock.payer_user_id)
            target_phone = await _phone(sessions, unlock.target_user_id)
            if unlock.purpose == DATING:
                payer = await dating.profile_of(unlock.payer_user_id)
                target = await dating.profile_of(unlock.target_user_id)
                await desk.notify(
                    unlock.payer_user_id,
                    f"Paiement reçu. Voici le numéro WhatsApp de "
                    f"{target.first_name if target else 'ton match'} : {target_phone}. "
                    "Écris-lui avec respect, et bonne rencontre !",
                    now=now,
                )
                await desk.notify(
                    unlock.target_user_id,
                    f"{payer.first_name if payer else 'Ton match'} a débloqué ton contact "
                    f"et va t'écrire. Son numéro : {payer_phone}.",
                    now=now,
                )
            else:
                await desk.notify(
                    unlock.payer_user_id,
                    f"Paiement reçu. Voici le numéro de la personne qui a trouvé ton "
                    f"document : {target_phone}. {HANDOVER}",
                    now=now,
                )
                await desk.notify(
                    unlock.target_user_id,
                    f"Le propriétaire du document que tu as déclaré a été vérifié par "
                    f"l'équipe Kora et va te contacter : {payer_phone}. {HANDOVER}",
                    now=now,
                )
        await uow.commit()


async def sweep_forever(
    database: Database, settings: Settings, *, interval: float = SWEEP_INTERVAL_SECONDS
) -> None:
    while True:
        try:
            await settle_once(database, settings)
        except asyncio.CancelledError:
            raise
        except Exception as exc:  # pragma: no cover - resilience path
            _logger.exception("unlock_payments_sweep_failed", error=str(exc))
        await asyncio.sleep(interval)
