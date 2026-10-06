"""Outbox dispatcher.

Reads unpublished rows in batches and forwards them to the broker. Rows are
claimed with ``SKIP LOCKED`` so several dispatchers can run concurrently
without publishing the same event twice.
"""

from __future__ import annotations

import asyncio

from sqlalchemy import func, select

from loka.shared.application.context import (
    reset_correlation_id,
    set_correlation_id,
)
from loka.shared.infrastructure.broker.topology import Broker
from loka.shared.infrastructure.db.engine import Database
from loka.shared.infrastructure.db.outbox import OutboxRecord, OutboxStore, to_integration_event
from loka.shared.infrastructure.logging import get_logger
from loka.shared.infrastructure.metrics import outbox_backlog

_logger = get_logger(__name__)

POLL_INTERVAL_SECONDS = 0.25
BATCH_SIZE = 200


class OutboxDispatcher:
    def __init__(
        self,
        database: Database,
        broker: Broker,
        *,
        batch_size: int = BATCH_SIZE,
        poll_interval: float = POLL_INTERVAL_SECONDS,
    ) -> None:
        self._database = database
        self._broker = broker
        self._batch_size = batch_size
        self._poll_interval = poll_interval
        self._running = False

    async def run_forever(self) -> None:
        self._running = True
        while self._running:
            try:
                dispatched = await self.dispatch_once()
                if dispatched == 0:
                    await asyncio.sleep(self._poll_interval)
            except asyncio.CancelledError:
                raise
            except Exception as exc:  # pragma: no cover - resilience path
                _logger.exception("outbox_dispatch_failed", error=str(exc))
                await asyncio.sleep(2.0)

    def stop(self) -> None:
        self._running = False

    async def dispatch_once(self) -> int:
        async with self._database.session() as session:
            store = OutboxStore(session)
            records = await store.claim_batch(self._batch_size)
            if not records:
                outbox_backlog.set(0)
                return 0

            published = 0
            for record in records:
                token = set_correlation_id(record.correlation_id)
                try:
                    envelope = to_integration_event(record)
                    await self._broker.publish(
                        routing_key=record.event_type,
                        payload=envelope.to_payload(),
                        headers={
                            "message_id": str(record.id),
                            "x-correlation-id": record.correlation_id,
                            "x-event-type": record.event_type,
                            "x-topic": record.topic,
                            "x-aggregate-version": str(record.aggregate_version),
                        },
                    )
                    await store.mark_published(record)
                    published += 1
                except Exception as exc:
                    # Several failure modes (timeouts, closed channels) carry an
                    # empty message; the type keeps the log and the stored
                    # last_error actionable.
                    reason = str(exc).strip() or type(exc).__name__
                    await store.mark_failed(record, reason)
                    _logger.warning(
                        "outbox_publish_failed",
                        event_type=record.event_type,
                        attempt=record.attempts,
                        error=reason,
                    )
                finally:
                    reset_correlation_id(token)

            outbox_backlog.set(len(records) - published)
            return published

    async def backlog_size(self) -> int:
        async with self._database.session() as session:
            result = await session.execute(
                select(func.count())
                .select_from(OutboxRecord)
                .where(OutboxRecord.published_at.is_(None))
            )
            return int(result.scalar_one())