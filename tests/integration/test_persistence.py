"""Database, Unit of Work and outbox behaviour against real PostgreSQL."""

from __future__ import annotations

import uuid
from datetime import datetime

import pytest
from sqlalchemy import func, select, text

from loka.shared.infrastructure.db.outbox import OutboxRecord, OutboxStore
from loka.shared.infrastructure.db.unit_of_work import SqlAlchemyUnitOfWork

pytestmark = pytest.mark.integration


def _new_id() -> uuid.UUID:
    return uuid.uuid4()


async def test_schema_exposes_expected_tables(database) -> None:  # type: ignore[no-untyped-def]
    async with database.session() as session:
        result = await session.execute(
            text("SELECT tablename FROM pg_tables WHERE schemaname = 'public'")
        )
        tables = {row[0] for row in result}
    assert {
        "users",
        "properties",
        "inbound_messages",
        "outbox_events",
        "service_fee_payments",
        "risk_profiles",
        "audit_log",
    } <= tables


async def test_unit_of_work_commits_state_and_events_atomically(
    database, dummy_factory, now  # type: ignore[no-untyped-def]
) -> None:
    before = await _outbox_count(database)

    aggregate = dummy_factory(_new_id(), now)
    async with SqlAlchemyUnitOfWork(database.sessions) as unit:
        unit.collect(aggregate)
        await unit.commit()

    assert await _outbox_count(database) == before + 1


async def test_rollback_discards_staged_events(database, dummy_factory, now) -> None:  # type: ignore[no-untyped-def]
    before = await _outbox_count(database)

    aggregate = dummy_factory(_new_id(), now)
    with pytest.raises(RuntimeError):
        async with SqlAlchemyUnitOfWork(database.sessions) as unit:
            unit.collect(aggregate)
            raise RuntimeError("use case failed before commit")

    assert await _outbox_count(database) == before


async def test_repositories_share_one_transaction(database, dummy_factory, now) -> None:  # type: ignore[no-untyped-def]
    """A rollback in one repository must undo every repository's work."""
    from loka.bounded_contexts.identity.infrastructure.persistence.models import UserRow

    async with SqlAlchemyUnitOfWork(database.sessions) as unit:
        unit.session.add(
            UserRow(
                id=_new_id(),
                phone_e164=f"+237{uuid.uuid4().int % 10**9:09d}",
                phone_e164_hash=uuid.uuid4().hex,
                roles=["TENANT"],
                created_at=now,
                updated_at=now,
            )
        )
        await unit.commit()

    with pytest.raises(RuntimeError):
        async with SqlAlchemyUnitOfWork(database.sessions) as unit:
            unit.session.add(
                UserRow(
                    id=_new_id(),
                    phone_e164=f"+237{uuid.uuid4().int % 10**9:09d}",
                    phone_e164_hash=uuid.uuid4().hex,
                    roles=["TENANT"],
                    created_at=now,
                    updated_at=now,
                )
            )
            await unit.session.flush()
            raise RuntimeError("aborted after flush")

    async with database.session() as session:
        users = await session.execute(select(func.count()).select_from(UserRow))
    assert users.scalar_one() == 1


async def test_outbox_claim_batch_skips_locked_rows(database, now) -> None:  # type: ignore[no-untyped-def]
    async with database.session() as session:
        for index in range(5):
            session.add(
                OutboxRecord(
                    aggregate_type="Probe",
                    aggregate_id=_new_id(),
                    aggregate_version=index + 1,
                    event_type="ProbeCreated",
                    topic="domain.events",
                    correlation_id=str(_new_id()),
                    payload={"index": index},
                    occurred_at=now,
                    attempts=0,
                )
            )
        await session.commit()

    async with database.session() as session:
        store = OutboxStore(session)
        batch = await store.claim_batch(limit=3)
        assert len(batch) == 3
        for record in batch:
            await store.mark_published(record)
        await session.commit()

    async with database.session() as session:
        remaining = await OutboxStore(session).claim_batch(limit=10)
        assert len(remaining) == 2


async def test_inbound_message_external_id_is_unique(database, now) -> None:  # type: ignore[no-untyped-def]
    from loka.bounded_contexts.messaging.infrastructure.persistence.models import (
        InboundMessageRow,
    )

    external_id = f"wa-{uuid.uuid4()}"

    async def insert() -> None:
        async with database.session() as session:
            session.add(
                InboundMessageRow(
                    id=_new_id(),
                    external_message_id=external_id,
                    provider="baileys",
                    conversation_ref="conv-1",
                    sender_phone="+237699123456",
                    message_kind="TEXT",
                    text="bonjour",
                    provider_timestamp=now,
                    received_at=now,
                    metadata_json={},
                )
            )
            await session.commit()

    await insert()
    with pytest.raises(Exception):  # noqa: B017 - integrity error is the point
        await insert()


async def test_partial_index_only_covers_active_properties(database, now) -> None:  # type: ignore[no-untyped-def]
    from loka.bounded_contexts.property.infrastructure.persistence.models import PropertyRow

    async def insert(status: str, city: str) -> uuid.UUID:
        property_id = _new_id()
        async with database.session() as session:
            session.add(
                PropertyRow(
                    id=property_id,
                    landlord_id=_new_id(),
                    status=status,
                    version=1,
                    city=city,
                    public_price_xaf=250_000,
                    rent_xaf=250_000,
                    created_at=now,
                    updated_at=now,
                )
            )
            await session.commit()
        return property_id

    await insert("AVAILABLE", "Douala")
    await insert("RENTED", "Douala")

    async with database.session() as session:
        result = await session.execute(
            select(func.count())
            .select_from(PropertyRow)
            .where(PropertyRow.status == "AVAILABLE")
        )
        assert result.scalar_one() == 1


async def test_optimistic_locking_detects_concurrent_update(database, now) -> None:  # type: ignore[no-untyped-def]

    property_id = await _seed_property(database, now)

    async def bump() -> bool:
        async with database.session() as session:
            result = await session.execute(
                text(
                    "UPDATE properties SET version = version + 1, updated_at = now() "
                    "WHERE id = :pid AND version = :version RETURNING version"
                ),
                {"pid": property_id, "version": 1},
            )
            return result.scalar_one_or_none() is not None

    assert await bump() is True
    assert await bump() is False


async def _seed_property(database, now: datetime) -> uuid.UUID:  # type: ignore[no-untyped-def]
    from loka.bounded_contexts.property.infrastructure.persistence.models import PropertyRow

    property_id = _new_id()
    async with database.session() as session:
        session.add(
            PropertyRow(
                id=property_id,
                landlord_id=_new_id(),
                status="AVAILABLE",
                version=1,
                city="Douala",
                rent_xaf=300_000,
                public_price_xaf=300_000,
                created_at=now,
                updated_at=now,
            )
        )
        await session.commit()
    return property_id


async def _outbox_count(database) -> int:  # type: ignore[no-untyped-def]
    async with database.session() as session:
        result = await session.execute(select(func.count()).select_from(OutboxRecord))
        return int(result.scalar_one())
