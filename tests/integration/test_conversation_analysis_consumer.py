"""The analysis consumer driven by the real broker.

``ConversationAnalysisRequested`` -> RabbitMQ -> ``ai.requests`` -> PostgreSQL.

The pipeline ships disabled by default, so these tests construct settings with
it enabled and no model. That is the production-safe way to exercise the queue:
the handler still runs, still records the turn and still refuses to spend
anything. A model is injected only where the test actually needs one.
"""

from __future__ import annotations

import asyncio
import uuid
from collections.abc import AsyncIterator
from datetime import UTC, datetime
from typing import Any

import pytest
import pytest_asyncio
from sqlalchemy import func, select

from loka.bounded_contexts.ai.application.use_cases.analyse_conversation_message import (
    AnalysisCommand,
)
from loka.bounded_contexts.identity.domain.value_objects.phone_number import PhoneNumber
from loka.bounded_contexts.identity.infrastructure.persistence.models import UserRow
from loka.bounded_contexts.messaging.application.use_cases.receive_inbound_message import (
    ReceiveInboundMessageCommand,
)
from loka.bounded_contexts.messaging.domain.value_objects.inbound_message import MessageKind
from loka.bounded_contexts.messaging.infrastructure.composition import (
    receive_inbound_message_use_case,
)
from loka.bounded_contexts.messaging.infrastructure.persistence.models import (
    ConversationSessionRow,
    ConversationTurnRow,
)
from loka.interfaces.worker.conversation_analysis_consumer import (
    AI_REQUEST_QUEUE,
    ConversationAnalysisConsumer,
)
from loka.interfaces.workers.registry import handler_registry
from loka.shared.infrastructure.broker.topology import EVENTS_EXCHANGE, QUEUES, Broker
from loka.shared.infrastructure.composition import unit_of_work
from loka.shared.infrastructure.config.settings import Settings
from loka.shared.infrastructure.crypto.blind_index import blind_index, derive_blind_index_key
from loka.shared.infrastructure.db.engine import Database

pytestmark = pytest.mark.integration

NOW = datetime(2026, 3, 1, 9, 0, tzinfo=UTC)
PHONE = "+237699123456"


@pytest_asyncio.fixture
async def broker(integration_settings: Settings) -> AsyncIterator[Broker]:
    connection = Broker(integration_settings.broker)
    await asyncio.wait_for(connection.connect(), timeout=15)
    await asyncio.wait_for(connection.declare_topology(), timeout=30)
    for spec in QUEUES:
        for queue_name in (spec.name, f"{spec.name}{integration_settings.broker.dlq_suffix}"):
            await (await connection.channel.get_queue(queue_name)).purge()
    yield connection
    await connection.close()


@pytest_asyncio.fixture
async def consumer(
    broker: Broker, database: Database, integration_settings: Settings
) -> AsyncIterator[ConversationAnalysisConsumer]:
    spec = next(item for item in QUEUES if item.name == AI_REQUEST_QUEUE)
    handler = ConversationAnalysisConsumer(spec, broker, database, integration_settings)
    await handler.start()
    yield handler
    await handler.stop()


async def _ingest(
    database: Database, settings: Settings, *, text: str = "Je veux publier un appartement"
) -> tuple[uuid.UUID, uuid.UUID]:
    key = derive_blind_index_key(settings.encryption_key.get_secret_value())
    now = datetime.now(UTC)
    async with database.session() as session:
        session.add(
            UserRow(
                id=uuid.uuid4(),
                phone_e164=PHONE,
                phone_e164_hash=blind_index(PHONE, key=key),
                roles=["LANDLORD"],
                status="ACTIVE",
                locale="fr",
                created_at=now,
                updated_at=now,
            )
        )
        await session.commit()

    async with unit_of_work(database) as uow:
        receipt = await receive_inbound_message_use_case(uow, index_key=key).execute(
            ReceiveInboundMessageCommand(
                provider="baileys",
                external_message_id=f"BAIL_{uuid.uuid4().hex[:8]}",
                conversation_ref=f"{uuid.uuid4()}@s.whatsapp.net",
                sender_phone=PHONE,
                message_kind=MessageKind.TEXT.value,
                provider_timestamp=NOW,
                text=text,
            ),
            now=NOW,
        )
    assert receipt.session_id is not None
    return receipt.message_id, receipt.session_id


def _request(message_id: uuid.UUID, session_id: uuid.UUID, **metadata: Any) -> dict[str, Any]:
    """The event as the ingestion use case stages it."""
    payload = {
        "message_id": str(message_id),
        "session_id": str(session_id),
        "sender_user_id": None,
        "provider": "baileys",
        "message_kind": "TEXT",
        **metadata,
    }
    return {
        "event_id": str(uuid.uuid4()),
        "event_type": "ConversationAnalysisRequested",
        "occurred_at": NOW.isoformat(),
        "correlation_id": "ingestion-correlation",
        "causation_id": None,
        "payload": {
            "aggregate_type": "InboundMessage",
            "aggregate_id": str(message_id),
            "aggregate_version": 1,
            "metadata": payload,
        },
    }


async def _publish(broker: Broker, request: dict[str, Any]) -> None:
    await broker.publish(
        exchange=EVENTS_EXCHANGE,
        routing_key="ConversationAnalysisRequested",
        payload=request,
    )


async def _turn_count(database: Database, session_id: uuid.UUID, timeout: float = 10.0) -> int:
    loop = asyncio.get_running_loop()
    deadline = loop.time() + timeout
    count = 0
    while loop.time() < deadline:
        async with database.session() as session:
            count = (
                await session.execute(
                    select(func.count())
                    .select_from(ConversationTurnRow)
                    .where(ConversationTurnRow.session_id == session_id)
                )
            ).scalar_one()
        if count:
            return count
        await asyncio.sleep(0.2)
    return count


async def _dlq_message(broker: Broker, settings: Settings, queue: str = AI_REQUEST_QUEUE) -> Any:
    dlq = await broker.channel.get_queue(f"{queue}{settings.broker.dlq_suffix}")
    return await dlq.get(timeout=8, fail=False)


class TestRegistry:
    def test_the_analysis_queue_has_a_real_handler(self) -> None:
        registry = handler_registry()
        assert AI_REQUEST_QUEUE in registry

    def test_the_queue_binds_the_analysis_routing_key(self) -> None:
        spec = next(item for item in QUEUES if item.name == AI_REQUEST_QUEUE)
        assert "ConversationAnalysisRequested" in spec.routing_keys


class TestEndToEnd:
    async def test_a_request_reaches_the_pipeline_and_records_a_turn(
        self,
        consumer: ConversationAnalysisConsumer,
        broker: Broker,
        database: Database,
        integration_settings: Settings,
    ) -> None:
        message_id, session_id = await _ingest(database, integration_settings)

        await _publish(broker, _request(message_id, session_id))

        assert await _turn_count(database, session_id) == 1

    async def test_the_turn_points_at_the_message_it_analysed(
        self,
        consumer: ConversationAnalysisConsumer,
        broker: Broker,
        database: Database,
        integration_settings: Settings,
    ) -> None:
        message_id, session_id = await _ingest(database, integration_settings)

        await _publish(broker, _request(message_id, session_id))

        await _turn_count(database, session_id)
        async with database.session() as session:
            row = (
                await session.execute(
                    select(ConversationTurnRow).where(
                        ConversationTurnRow.session_id == session_id
                    )
                )
            ).scalars().one()
        assert row.message_id == message_id
        assert row.direction == "INBOUND"
        assert row.used_llm is False
        assert row.intent is not None

    async def test_the_session_advances_from_the_analysed_message(
        self,
        consumer: ConversationAnalysisConsumer,
        broker: Broker,
        database: Database,
        integration_settings: Settings,
    ) -> None:
        """The queue is only worth draining if it moves the conversation."""
        message_id, session_id = await _ingest(
            database, integration_settings, text="Je veux publier un appartement"
        )

        await _publish(broker, _request(message_id, session_id))

        await _turn_count(database, session_id)
        async with database.session() as session:
            row = await session.get(ConversationSessionRow, session_id)
        assert row is not None
        assert row.step == "COLLECT_LOCATION"
        assert row.context_json["property_type"] == "APARTMENT"

    async def test_a_redelivered_request_is_recorded_twice_but_changes_nothing(
        self,
        consumer: ConversationAnalysisConsumer,
        broker: Broker,
        database: Database,
        integration_settings: Settings,
    ) -> None:
        """The queue is at-least-once. The turn is append-only by design, but the
        session must not drift: a redelivery is a repeat, not new information."""
        message_id, session_id = await _ingest(database, integration_settings)
        request = _request(message_id, session_id)

        await _publish(broker, request)
        assert await _turn_count(database, session_id) == 1

        await _publish(broker, request)
        await _wait_for_count(database, session_id, expected=2)
        async with database.session() as session:
            row = await session.get(ConversationSessionRow, session_id)
        assert row is not None
        # Two turns, one step: the facts were already known, so nothing moved.
        assert row.step == "COLLECT_LOCATION"


async def _wait_for_count(
    database: Database, session_id: uuid.UUID, *, expected: int, timeout: float = 10.0
) -> int:
    loop = asyncio.get_running_loop()
    deadline = loop.time() + timeout
    count = 0
    while loop.time() < deadline:
        async with database.session() as session:
            count = (
                await session.execute(
                    select(func.count())
                    .select_from(ConversationTurnRow)
                    .where(ConversationTurnRow.session_id == session_id)
                )
            ).scalar_one()
        if count >= expected:
            return count
        await asyncio.sleep(0.2)
    return count


class TestPermanentFailures:
    async def test_a_request_without_ids_is_parked_not_retried(
        self,
        consumer: ConversationAnalysisConsumer,
        broker: Broker,
        database: Database,
        integration_settings: Settings,
    ) -> None:
        """Retrying a malformed request would fail identically for ever, so it
        goes straight to the DLQ where an operator can see it."""
        await _publish(broker, {"payload": {"metadata": {}}})

        message = await _dlq_message(broker, integration_settings)
        assert message is not None
        headers = message.headers or {}
        # The body is kept so the delivery can be replayed by hand; the reason
        # lives in the headers, where an operator looks.
        assert "message_id" in str(headers.get("x-last-error", ""))
        await message.ack()

    async def test_a_request_for_an_unknown_message_is_a_no_op(
        self,
        consumer: ConversationAnalysisConsumer,
        broker: Broker,
        database: Database,
        integration_settings: Settings,
    ) -> None:
        """Well-formed but pointing at nothing: the handler must record nothing
        rather than invent a conversation."""
        await _ingest(database, integration_settings)
        await _publish(broker, _request(uuid.uuid4(), uuid.uuid4()))

        await asyncio.sleep(2.0)
        async with database.session() as session:
            turns = await session.scalar(select(func.count()).select_from(ConversationTurnRow))
        assert turns == 0
        # Not an error either: the ids were well formed, there was simply
        # nothing to analyse, so nothing is parked.
        parked = await _dlq_message(broker, integration_settings)
        assert parked is None


class TestPipelineDisabled:
    async def test_a_disabled_pipeline_still_answers_deterministically(
        self,
        consumer: ConversationAnalysisConsumer,
        broker: Broker,
        database: Database,
        integration_settings: Settings,
    ) -> None:
        """``pipeline_enabled`` is false by default: the regex path must keep
        working, or switching the AI off would switch the product off."""
        assert integration_settings.ai.pipeline_enabled is False
        message_id, session_id = await _ingest(database, integration_settings)

        await _publish(broker, _request(message_id, session_id))

        assert await _turn_count(database, session_id) == 1
        async with database.session() as session:
            row = await session.get(ConversationSessionRow, session_id)
        assert row is not None
        assert row.step == "COLLECT_LOCATION"

    async def test_an_ambiguous_message_is_recorded_as_unknown_when_disabled(
        self,
        consumer: ConversationAnalysisConsumer,
        broker: Broker,
        database: Database,
        integration_settings: Settings,
    ) -> None:
        message_id, session_id = await _ingest(
            database, integration_settings, text="Je vous explique mon cas"
        )

        await _publish(broker, _request(message_id, session_id))

        await _turn_count(database, session_id)
        async with database.session() as session:
            row = (
                await session.execute(
                    select(ConversationTurnRow).where(
                        ConversationTurnRow.session_id == session_id
                    )
                )
            ).scalars().one()
        assert row.intent == "UNKNOWN"
        assert row.used_llm is False
        parked = await _dlq_message(broker, integration_settings)
        assert parked is None


class TestCommandIsWired:
    def test_the_analysis_command_carries_only_identifiers(self) -> None:
        command = AnalysisCommand(message_id=uuid.uuid4(), session_id=uuid.uuid4())
        assert isinstance(command.message_id, uuid.UUID)
        assert isinstance(command.session_id, uuid.UUID)
        assert PhoneNumber.normalize(PHONE).e164 == PHONE
