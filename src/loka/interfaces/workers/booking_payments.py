"""The sweep that turns a validated Mobile Money withdrawal into a ticket.

Every few seconds it asks the provider about each reservation awaiting
payment: paid, the traveller gets his ticket and the agency its passenger;
failed or past its hold, the seats are released and the traveller is told.
"""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime

from loka.bounded_contexts.booking.conversation import texts
from loka.bounded_contexts.booking.domain.reservation import EXPIRED
from loka.bounded_contexts.booking.infrastructure.store import SqlAlchemyBookingStore
from loka.bounded_contexts.conversation.application.plugs.desk import ConversationDesk
from loka.bounded_contexts.conversation.infrastructure.messaging_composition import (
    CONVERSATION_SESSION_REPOSITORY,
)
from loka.bounded_contexts.payment.infrastructure.mobile_money import MobileMoneyPayments
from loka.shared.infrastructure.composition import payment_gateway, unit_of_work
from loka.shared.infrastructure.config.settings import Settings
from loka.shared.infrastructure.db.engine import Database
from loka.shared.infrastructure.logging import get_logger

_logger = get_logger(__name__)

SWEEP_INTERVAL_SECONDS = 10.0


async def settle_once(database: Database, settings: Settings) -> None:
    payments = MobileMoneyPayments(payment_gateway(settings))
    now = datetime.now(UTC)
    async with unit_of_work(database) as uow:
        store = SqlAlchemyBookingStore(uow.session)
        desk = ConversationDesk(uow, uow.repository(CONVERSATION_SESSION_REPOSITORY))
        for reservation in await store.awaiting_payment():
            status = (
                await payments.status(reservation.payment_reference)
                if reservation.payment_reference
                else None
            )
            line = await store.line(reservation.line_id)
            agency = await store.agency(reservation.agency_id)
            if status == "SUCCEEDED" and line is not None:
                reservation.paid()
                await store.save(reservation)
                await desk.notify(
                    reservation.client_user_id,
                    texts.ticket(
                        reservation,
                        agency.name if agency else "Agence",
                        line.origin,
                        line.destination,
                    ),
                    now=now,
                )
                if agency is not None:
                    await desk.notify_phone(
                        agency.phone,
                        texts.to_agency(reservation, line.origin, line.destination),
                        now=now,
                    )
            elif status in ("FAILED", "EXPIRED") or reservation.held_until <= now:
                reservation.status = EXPIRED
                await store.save(reservation)
                await desk.notify(reservation.client_user_id, texts.lapsed(), now=now)
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
            _logger.exception("booking_payments_sweep_failed", error=str(exc))
        await asyncio.sleep(interval)
