"""Inbound WhatsApp ingestion against real PostgreSQL.

The de-duplication guarantee lives in a unique constraint, so it is tested
against the real database and not against a fake.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError

from loka.bounded_contexts.identity.domain.value_objects.phone_number import PhoneNumber
from loka.bounded_contexts.identity.infrastructure.persistence.models import UserRow
from loka.bounded_contexts.messaging.application.use_cases.receive_inbound_message import (
    ReceiveInboundMessageCommand,
)
from loka.bounded_contexts.messaging.domain.entities.conversation_session import (
    SESSION_IDLE_TIMEOUT,
    FlowStep,
)
from loka.bounded_contexts.messaging.infrastructure.composition import (
    receive_inbound_message_use_case,
)
from loka.bounded_contexts.messaging.infrastructure.persistence.models import (
    ConversationSessionRow,
    InboundMessageRow,
)
from loka.shared.domain.errors import ConcurrencyConflict
from loka.shared.infrastructure.composition import unit_of_work
from loka.shared.infrastructure.config.settings import Settings
from loka.shared.infrastructure.crypto.blind_index import (
    blind_index,
    derive_blind_index_key,
)
from loka.shared.infrastructure.db.engine import Database
from loka.shared.infrastructure.db.outbox import OutboxRecord
from loka.shared.infrastructure.db.unit_of_work import SqlAlchemyUnitOfWork

pytestmark = pytest.mark.integration

NOW = datetime(2026, 3, 1, 9, 0, tzinfo=UTC)
PHONE = "+237699123456"


def _key(settings: Settings) -> bytes:
    return derive_blind_index_key(settings.encryption_key.get_secret_value())


async def _user(database: Database, settings: Settings, *, phone: str = PHONE) -> uuid.UUID:
    """Create the account behind a phone number, indexed like production does."""
    user_id = uuid.uuid4()
    now = datetime.now(UTC)
    async with database.session() as session:
        session.add(
            UserRow(
                id=user_id,
                phone_e164=phone,
                phone_e164_hash=blind_index(phone, key=_key(settings)),
                roles=["LANDLORD"],
                status="ACTIVE",
                locale="fr",
                created_at=now,
                updated_at=now,
            )
        )
        await session.commit()
    return user_id


def _command(**overrides: object) -> ReceiveInboundMessageCommand:
    payload: dict[str, object] = {
        "provider": "baileys",
        "external_message_id": "BAIL_E1_REAL",
        "conversation_ref": "699123456@s.whatsapp.net",
        "sender_phone": "699 12 34 56",
        "message_kind": "TEXT",
        "provider_timestamp": NOW,
        "text": "Je veux publier un appartement",
    }
    payload.update(overrides)
    return ReceiveInboundMessageCommand(**payload)  # type: ignore[arg-type]


async def _execute(
    database: Database, settings: Settings, command: ReceiveInboundMessageCommand, *, now: datetime
) -> object:
    async with unit_of_work(database) as uow:
        return await receive_inbound_message_use_case(uow, index_key=_key(settings)).execute(
            command, now=now
        )


async def test_a_message_is_stored_with_its_session_and_event(
    database: Database, integration_settings: Settings
) -> None:
    user_id = await _user(database, integration_settings)

    receipt = await _execute(database, integration_settings, _command(), now=NOW)

    assert receipt.duplicated is False
    assert receipt.sender_user_id == user_id
    async with database.session() as session:
        row = await session.get(InboundMessageRow, receipt.message_id)
        assert row is not None
        assert row.session_id == receipt.session_id
        assert row.sender_user_id == user_id
        assert row.processed is False
        assert row.message_kind == "TEXT"
        assert row.text == "Je veux publier un appartement"
        assert row.metadata_json["raw_kind"] == "TEXT"

        events = await session.execute(
            select(OutboxRecord).where(OutboxRecord.event_type == "WhatsAppMessageIngested")
        )
        records = list(events.scalars())
        assert len(records) == 1
        assert records[0].aggregate_id == receipt.message_id
        assert records[0].topic == "whatsapp.events"
        assert records[0].payload["metadata"]["session_id"] == str(receipt.session_id)
        assert "appartement" not in str(records[0].payload)


async def test_the_same_message_twice_is_stored_once(
    database: Database, integration_settings: Settings
) -> None:
    """A gateway retry must not produce a second row, session or event."""
    first = await _execute(database, integration_settings, _command(), now=NOW)

    second = await _execute(
        database,
        integration_settings,
        _command(text="Je veux publier un appartement"),
        now=NOW + timedelta(seconds=1),
    )

    assert second.duplicated is True
    assert second.message_id == first.message_id
    async with database.session() as session:
        assert await session.scalar(
            select(func.count()).select_from(InboundMessageRow)
        ) == 1
        assert await session.scalar(select(func.count()).select_from(ConversationSessionRow)) == 1
        assert (
            await session.scalar(
                select(func.count())
                .select_from(OutboxRecord)
                .where(OutboxRecord.event_type == "WhatsAppMessageIngested")
            )
            == 1
        )


async def test_the_unique_constraint_rejects_a_parallel_worker(
    database: Database, integration_settings: Settings
) -> None:
    """Defence in depth: even a bypass of the use case cannot duplicate a row."""
    receipt = await _execute(database, integration_settings, _command(), now=NOW)

    async with database.session() as session:
        stored = await session.get(InboundMessageRow, receipt.message_id)
        assert stored is not None
        duplicate = {
            column.name: getattr(stored, column.name)
            for column in InboundMessageRow.__table__.columns
            if column.name != "id"
        }
        duplicate["id"] = uuid.uuid4()
        with pytest.raises(IntegrityError, match="uq_inbound_external_message"):
            await session.execute(InboundMessageRow.__table__.insert().values(**duplicate))
            await session.commit()


async def test_an_unknown_number_is_stored_without_an_account(
    database: Database, integration_settings: Settings
) -> None:
    receipt = await _execute(database, integration_settings, _command(), now=NOW)

    assert receipt.sender_user_id is None
    async with database.session() as session:
        row = await session.get(InboundMessageRow, receipt.message_id)
        assert row is not None and row.sender_user_id is None


async def test_the_account_is_found_through_the_blind_index(
    database: Database, integration_settings: Settings
) -> None:
    """Whatever formatting the sender used, the same account is resolved."""
    user_id = await _user(database, integration_settings)

    receipt = await _execute(
        database,
        integration_settings,
        _command(sender_phone="+237 6 99 12 34 56", external_message_id="BAIL_E1_FORMATTED"),
        now=NOW,
    )

    assert receipt.sender_user_id == user_id


async def test_follow_up_messages_join_the_same_session(
    database: Database, integration_settings: Settings
) -> None:
    first = await _execute(database, integration_settings, _command(), now=NOW)

    second = await _execute(
        database,
        integration_settings,
        _command(external_message_id="BAIL_E1_SECOND", text="Deux chambres svp"),
        now=NOW + timedelta(minutes=3),
    )

    assert second.session_id == first.session_id
    async with database.session() as session:
        assert (
            await session.scalar(select(func.count()).select_from(ConversationSessionRow)) == 1
        )


async def test_a_session_advances_under_optimistic_concurrency(
    database: Database, integration_settings: Settings
) -> None:
    await _execute(database, integration_settings, _command(), now=NOW)
    async with database.session() as session:
        stored = await session.scalar(
            select(ConversationSessionRow).where(ConversationSessionRow.phone_e164 == PHONE)
        )
        assert stored is not None
        stored.step = FlowStep.COLLECT_PRICE.value
        stored.revision += 1
        await session.commit()

    from loka.bounded_contexts.messaging.infrastructure.persistence import (
        conversation_session_repository,
    )

    repository_type = conversation_session_repository.SqlAlchemyConversationSessionRepository


    async with SqlAlchemyUnitOfWork(database.sessions) as uow:
        repository = repository_type(uow.session)
        session_aggregate = await repository.latest_by_phone(PhoneNumber(e164=PHONE))
        assert session_aggregate is not None
        stale_revision = session_aggregate.revision
        session_aggregate.transition(FlowStep.COLLECT_LOCATION, now=NOW)
        await repository.save(session_aggregate, expected_revision=stale_revision)
        await uow.commit()

    async with SqlAlchemyUnitOfWork(database.sessions) as uow:
        repository = repository_type(uow.session)
        other = await repository.latest_by_phone(PhoneNumber(e164=PHONE))
        assert other is not None
        assert other.revision == stale_revision + 1
        other.transition(FlowStep.DONE, now=NOW)
        with pytest.raises(ConcurrencyConflict):
            # Reusing the revision read before the first worker committed must
            # be refused instead of overwriting its step.
            await repository.save(other, expected_revision=stale_revision)


async def test_an_idle_session_is_restarted_and_stays_one_row(
    database: Database, integration_settings: Settings
) -> None:
    first = await _execute(database, integration_settings, _command(), now=NOW)
    later = NOW + SESSION_IDLE_TIMEOUT + timedelta(minutes=5)

    second = await _execute(
        database,
        integration_settings,
        _command(external_message_id="BAIL_E1_AFTER_IDLE", text="Bonjour encore"),
        now=later,
    )

    assert second.session_id == first.session_id
    async with database.session() as session:
        assert (
            await session.scalar(select(func.count()).select_from(ConversationSessionRow)) == 1
        )
        stored = await session.scalar(
            select(ConversationSessionRow).where(ConversationSessionRow.id == first.session_id)
        )
        assert stored is not None
        assert stored.step == FlowStep.START.value
        assert stored.context_json == {}


async def test_the_message_and_its_event_share_one_transaction(
    database: Database, integration_settings: Settings, monkeypatch: pytest.MonkeyPatch
) -> None:
    """If the event cannot be written, the message must not be either."""
    from loka.shared.infrastructure.db import outbox as outbox_module

    async def failing_append(
        self: outbox_module.OutboxStore, events: object, *, correlation_id: str
    ) -> None:
        raise RuntimeError("outbox is unavailable")

    monkeypatch.setattr(outbox_module.OutboxStore, "append", failing_append)

    with pytest.raises(RuntimeError, match="outbox is unavailable"):
        await _execute(database, integration_settings, _command(), now=NOW)

    async with database.session() as session:
        assert await session.scalar(select(func.count()).select_from(InboundMessageRow)) == 0
        assert (
            await session.scalar(
                select(func.count())
                .select_from(OutboxRecord)
                .where(OutboxRecord.event_type == "WhatsAppMessageIngested")
            )
            == 0
        )


async def test_media_only_messages_are_ingested(
    database: Database, integration_settings: Settings
) -> None:
    receipt = await _execute(
        database,
        integration_settings,
        _command(text=None, media_object_key="inbound/2026/03/photo.jpg", message_kind="IMAGE"),
        now=NOW,
    )

    async with database.session() as session:
        row = await session.get(InboundMessageRow, receipt.message_id)
        assert row is not None
        assert row.text is None
        assert row.media_object_key == "inbound/2026/03/photo.jpg"


async def test_the_provider_timestamp_is_bounded(
    database: Database, integration_settings: Settings
) -> None:
    receipt = await _execute(
        database,
        integration_settings,
        _command(provider_timestamp=NOW + timedelta(days=9000)),
        now=NOW,
    )

    async with database.session() as session:
        row = await session.get(InboundMessageRow, receipt.message_id)
        assert row is not None
        assert abs(row.provider_timestamp - NOW) <= timedelta(days=7)
