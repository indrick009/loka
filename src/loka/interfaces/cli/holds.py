"""See the conversations held for a person, and release them.

A conversation is held when a reply showed it going round in circles (see
``interfaces/worker/stuck_guard``): the user was told an answer is coming and
idia stopped answering. Once the defect is understood — the journal has the
``incident`` line — and fixed:

    python -m loka.interfaces.cli.holds list
    python -m loka.interfaces.cli.holds release +237600000000
    python -m loka.interfaces.cli.holds release +237600000000 --retry
    python -m loka.interfaces.cli.holds release +237600000000 --say "Voici…"

``release`` answers the last message the user sent while held. When he sent
none, nothing is replayed unless asked: ``--retry`` reads again the message
whose reply was withheld, ``--say`` sends a sentence written by hand.
``--retry`` runs a turn whose facts were already recorded — safe for an
answer or a question, to be avoided right after an order or a payment.
"""

from __future__ import annotations

import argparse
import asyncio
import uuid
from datetime import UTC, datetime

from redis.asyncio import Redis
from sqlalchemy import select

from loka.bounded_contexts.conversation.application.use_cases.analyse_conversation_message import (
    AnalysisCommand,
)
from loka.bounded_contexts.conversation.domain.events.message_events import (
    WhatsAppMessageSendRequested,
)
from loka.bounded_contexts.conversation.infrastructure.adapters.redis_holds import (
    Hold,
    RedisConversationHolds,
)
from loka.bounded_contexts.conversation.infrastructure.composition import (
    analyse_conversation_message_use_case,
)
from loka.bounded_contexts.conversation.infrastructure.persistence.conversation_session_repository import (  # noqa: E501
    SqlAlchemyConversationSessionRepository,
)
from loka.bounded_contexts.conversation.infrastructure.persistence.messaging_models import (
    InboundMessageRow,
)
from loka.interfaces.worker.conversation_analysis_consumer import redis_lock_factory
from loka.shared.infrastructure.composition import unit_of_work
from loka.shared.infrastructure.config.settings import Settings, get_settings
from loka.shared.infrastructure.conversation_log import journal
from loka.shared.infrastructure.db.engine import Database


async def _last_message(database: Database, session_id: uuid.UUID) -> uuid.UUID | None:
    async with unit_of_work(database) as uow:
        found: uuid.UUID | None = (
            await uow.session.execute(
                select(InboundMessageRow.id)
                .where(InboundMessageRow.session_id == session_id)
                .order_by(InboundMessageRow.received_at.desc())
                .limit(1)
            )
        ).scalar_one_or_none()
        return found


async def _say(database: Database, session_id: uuid.UUID, text: str) -> None:
    now = datetime.now(UTC)
    async with unit_of_work(database) as uow:
        session = await SqlAlchemyConversationSessionRepository(uow.session).get_by_id(session_id)
        if session is None:
            raise SystemExit(f"session {session_id} no longer exists")
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
            correlation_id=f"hold-release-{session.id}",
        )
        await uow.commit()


async def _answer(
    settings: Settings, database: Database, hold: Hold, message_id: uuid.UUID
) -> None:
    session_id = uuid.UUID(hold.session_id)
    # The same lock as the worker: a message arriving now waits its turn.
    lock = redis_lock_factory(settings)(f"conversation:{session_id}")
    async with lock, unit_of_work(database) as uow:
        report = await analyse_conversation_message_use_case(uow, settings=settings).execute(
            AnalysisCommand(message_id=message_id, session_id=session_id),
            now=datetime.now(UTC),
        )
    print(f"  answered: intent={report.intent} accepted={report.accepted}")


async def run(settings: Settings, args: argparse.Namespace) -> None:
    holds = RedisConversationHolds(Redis.from_url(settings.redis_url, decode_responses=True))
    held = await holds.all()
    if args.command == "list":
        for hold in held:
            print(f"{hold.incident or '-'}  {hold.phone}  {hold.signal}  since {hold.since}")
            print(f"    withheld: {hold.withheld}")
            if hold.note:
                print(f"    note: {hold.note}")
        print(f"{len(held)} conversation(s) held")
        return
    wanted = args.phone.lstrip("+").upper()
    found = next(
        (h for h in held if wanted in (h.phone.lstrip("+"), h.incident.upper())), None
    )
    if found is None:
        raise SystemExit(f"no held conversation for {args.phone}")
    database = Database(settings.database, application_name="loka-holds")
    await database.connect()
    try:
        session_id = uuid.UUID(found.session_id)
        # Released first: a reply to a held thread is dropped by the worker.
        await holds.release(session_id)
        journal("released", found.phone, signal=found.signal, said=args.say, retry=args.retry)
        print(f"released {found.phone}")
        if args.say:
            await _say(database, session_id, args.say)
            print("  sentence queued")
            return
        message_id = uuid.UUID(found.pending_message_id) if found.pending_message_id else None
        if message_id is None and args.retry:
            message_id = await _last_message(database, session_id)
        if message_id is None:
            print("  nothing replayed (no message while held; use --retry or --say)")
            return
        await _answer(settings, database, found, message_id)
    finally:
        await database.dispose()


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(description="Conversations held for a person.")
    commands = parser.add_subparsers(dest="command", required=True)
    commands.add_parser("list", help="the conversations currently held")
    release = commands.add_parser("release", help="let idia answer this conversation again")
    release.add_argument("phone", help="the user's number or the incident, as printed by list")
    release.add_argument("--retry", action="store_true", help="read the withheld turn again")
    release.add_argument("--say", help="send this sentence instead of replaying a message")
    asyncio.run(run(get_settings(), parser.parse_args(argv)))


if __name__ == "__main__":
    main()
