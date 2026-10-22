"""The landlord's question, carried from a tenant's choice to his own thread.

A tenant picked a lodging, so its landlord must be asked. He is not answering
anything — nothing of his triggered this — so the question cannot be staged as
a reply to a turn of his. It goes out against the thread he last used.

Two translations stand between the event and the message, and one of them has
already bitten: a listing names a landlord *profile*, a WhatsApp thread belongs
to an *account*, and the account is what holds the phone number. Both are
exercised here with deliberately different ids.
"""

from __future__ import annotations

import asyncio
import uuid
from collections.abc import AsyncIterator
from datetime import UTC, datetime
from typing import Any

import pytest
import pytest_asyncio
from sqlalchemy import select

from loka.bounded_contexts.identity.infrastructure.persistence.models import UserRow
from loka.bounded_contexts.landlord.infrastructure.persistence.models import (
    LandlordProfileRow,
)
from loka.bounded_contexts.messaging.infrastructure.persistence.models import (
    ConversationSessionRow,
)
from loka.interfaces.worker.availability_question_consumer import (
    AVAILABILITY_QUESTION_QUEUE,
    AvailabilityQuestionConsumer,
)
from loka.shared.infrastructure.broker.topology import QUEUES, Broker
from loka.shared.infrastructure.config.settings import Settings
from loka.shared.infrastructure.db.outbox import OutboxRecord

pytestmark = pytest.mark.integration

NOW = datetime(2026, 3, 1, 9, 0, tzinfo=UTC)
LANDLORD_PHONE = "+237699111222"


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
    broker: Broker, database: Any, integration_settings: Any
) -> AsyncIterator[Any]:
    spec = next(item for item in QUEUES if item.name == AVAILABILITY_QUESTION_QUEUE)
    handler = AvailabilityQuestionConsumer(spec, broker, database, integration_settings)
    await handler.start()
    yield handler
    await handler.stop()


async def _landlord_with_a_thread(database: Any) -> tuple[uuid.UUID, uuid.UUID]:
    """An account, its landlord profile and the thread it last wrote in.

    The two ids are different on purpose: that difference is what the consumer
    has to translate, and making them equal in a test hides the only bug worth
    catching here.
    """
    account_id, profile_id = uuid.uuid4(), uuid.uuid4()
    async with database.session() as session:
        session.add(
            UserRow(
                id=account_id,
                phone_e164=LANDLORD_PHONE,
                phone_e164_hash=uuid.uuid4().hex,
                display_name="Bailleur",
                status="ACTIVE",
                created_at=NOW,
                updated_at=NOW,
            )
        )
        session.add(
            LandlordProfileRow(
                id=profile_id,
                user_id=account_id,
                verification_status="VERIFIED",
                created_at=NOW,
                updated_at=NOW,
            )
        )
        session.add(
            ConversationSessionRow(
                id=uuid.uuid4(),
                user_id=account_id,
                phone_e164=LANDLORD_PHONE,
                flow="PROPERTY_CREATION",
                step="PUBLISHED",
                status="IDLE",
                language="fr",
                context_json={},
                revision=1,
                started_at=NOW,
                last_step_at=NOW,
                updated_at=NOW,
            )
        )
        await session.commit()
    return account_id, profile_id


def _event(*, property_id: uuid.UUID, landlord_id: uuid.UUID) -> dict[str, Any]:
    check_id = uuid.uuid4()
    return {
        "message_id": str(uuid.uuid4()),
        "event_type": "AvailabilityCheckRequested",
        "occurred_at": NOW.isoformat(),
        "correlation_id": "availability-question-test",
        "causation_id": None,
        "payload": {
            "event_id": str(uuid.uuid4()),
            "event_type": "AvailabilityCheckRequested",
            "aggregate_type": "AvailabilityCheck",
            "aggregate_id": str(check_id),
            "aggregate_version": 1,
            "occurred_at": NOW.isoformat(),
            "actor_id": None,
            "metadata": {
                "check_id": str(check_id),
                "property_id": str(property_id),
                "landlord_id": str(landlord_id),
            },
        },
    }


async def _staged_question(database: Any) -> OutboxRecord | None:
    for _ in range(60):
        async with database.session() as session:
            row = (
                await session.execute(
                    select(OutboxRecord).where(
                        OutboxRecord.event_type == "WhatsAppMessageSendRequested"
                    )
                )
            ).scalars().first()
        if row is not None:
            return row
        await asyncio.sleep(0.25)
    return None


async def test_the_landlord_receives_the_question_on_his_own_thread(
    consumer: Any, broker: Any, database: Any
) -> None:
    _, profile_id = await _landlord_with_a_thread(database)

    await broker.publish(
        routing_key="AvailabilityCheckRequested",
        payload=_event(property_id=uuid.uuid4(), landlord_id=profile_id),
    )

    staged = await _staged_question(database)
    assert staged is not None
    payload = dict(staged.payload["metadata"])
    assert payload["recipient_phone"] == LANDLORD_PHONE
    assert payload["question_key"] == "ask.availability_check"
    # Platform text, never the model's: the wording of this question is the
    # rule. No listing in the catalogue here, so nothing names it.
    assert payload["text"].startswith(
        "Le logement est encore disponible ? Un locataire aimerait visiter."
    )


async def test_a_landlord_profile_nobody_owns_is_parked_not_retried(
    consumer: Any, broker: Any, database: Any, integration_settings: Any
) -> None:
    """No amount of waiting conjures an account for an orphan profile."""
    await broker.publish(
        routing_key="AvailabilityCheckRequested",
        payload=_event(property_id=uuid.uuid4(), landlord_id=uuid.uuid4()),
    )

    dlq = f"{AVAILABILITY_QUESTION_QUEUE}{integration_settings.broker.dlq_suffix}"
    queue = await broker.channel.get_queue(dlq)
    for _ in range(60):
        message = await queue.get(fail=False, timeout=5)
        if message is not None:
            await message.ack()
            return
        await asyncio.sleep(0.25)
    raise AssertionError("the unanswerable question never reached the DLQ")
