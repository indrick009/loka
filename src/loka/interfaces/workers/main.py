"""Worker bootstrap.

One process per queue family. Consumers are idempotent, so scaling out means
starting more processes, not changing code.
"""

from __future__ import annotations

import asyncio
import contextlib
import signal

from loka.bounded_contexts.ai.infrastructure.composition import validate_ai_settings
from loka.interfaces.workers.registry import handler_registry
from loka.shared.infrastructure.broker.topology import Broker
from loka.shared.infrastructure.config.settings import get_settings
from loka.shared.infrastructure.db.engine import Database
from loka.shared.infrastructure.logging import configure_logging, get_logger
from loka.shared.infrastructure.worker.consumers import build_consumers
from loka.shared.infrastructure.worker.outbox_dispatcher import OutboxDispatcher


async def run() -> None:
    settings = get_settings()
    configure_logging(
        level=settings.observability.log_level,
        json_output=settings.observability.log_json,
    )
    logger = get_logger("worker")

    # Before any consumer starts: a worker that boots with an AI pipeline it
    # cannot run safely would accept traffic and then answer every landlord
    # badly, which is worse than refusing to start.
    validate_ai_settings(settings)

    database = Database(settings.database, application_name=f"{settings.service_name}-worker")
    await database.connect()
    broker = Broker(settings.broker)
    await broker.connect()
    await broker.declare_topology()

    consumers = build_consumers(broker, database, settings, handler_registry())
    for consumer in consumers:
        await consumer.start()

    stop = asyncio.Event()
    loop = asyncio.get_running_loop()
    for sig in (signal.SIGINT, signal.SIGTERM):
        loop.add_signal_handler(sig, stop.set)

    logger.info("worker_started", queues=[c.queue_spec.name for c in consumers])

    # Rows committed by a consumer only reach the broker through the outbox, so
    # without this loop the AI analysis, the outbound WhatsApp replies and every
    # projection stay silent while the workers look healthy. Claiming is done
    # with SKIP LOCKED, so a second worker process publishes nothing twice.
    dispatcher = OutboxDispatcher(database, broker)
    dispatch_task = asyncio.create_task(dispatcher.run_forever(), name="outbox-dispatcher")
    logger.info("outbox_dispatcher_started")

    try:
        await stop.wait()
    finally:
        dispatch_task.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await dispatch_task
        for consumer in consumers:
            await consumer.stop()
        await broker.close()
        await database.dispose()
        logger.info("worker_stopped")


def main() -> None:
    asyncio.run(run())


if __name__ == "__main__":
    main()