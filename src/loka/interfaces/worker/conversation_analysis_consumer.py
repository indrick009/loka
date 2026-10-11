"""Conversation analysis handler.

Drains ``ai.requests``, whose messages are the ``ConversationAnalysisRequested``
events staged by the ingestion use case. It re-reads the message through the
messaging repository instead of trusting the event: the event carries ids only,
so the text stays in exactly one place.

What it refuses to do is invent. A payload without the ids of an existing
message and session is a permanent failure and goes to the DLQ, because retrying
it would fail identically for ever. A model outage, on the other hand, is
transient and must be retried.
"""

from __future__ import annotations

import uuid
from collections.abc import Callable
from contextlib import AsyncExitStack
from datetime import UTC, datetime
from typing import Any, Protocol

from redis.asyncio import Redis

from loka.bounded_contexts.ai.application.use_cases.analyse_conversation_message import (
    AnalysisCommand,
    AnalysisReport,
)
from loka.bounded_contexts.ai.infrastructure.composition import (
    analyse_conversation_message_use_case,
)
from loka.shared.infrastructure.broker.consumer import (
    ConsumerBase,
    PermanentHandlerError,
    RetryableHandlerError,
)
from loka.shared.infrastructure.broker.topology import Broker, QueueSpec
from loka.shared.infrastructure.composition import unit_of_work
from loka.shared.infrastructure.config.settings import Settings
from loka.shared.infrastructure.db.engine import Database
from loka.shared.infrastructure.logging import get_logger
from loka.shared.infrastructure.redis_client import (
    LockBackendUnavailable,
    NotAcquired,
    RedisLock,
)

AI_REQUEST_QUEUE = "ai.requests"

# Long enough to cover a whole turn: an intent call, a fallback model and a
# reply call all sit inside it. A lock that expires mid-analysis puts the race
# straight back, so the ceiling is generous rather than tight — a stale lock
# costs one delayed turn, a short one costs a lost reply.
LOCK_TTL_MS = 60_000

# Wait for the lock rather than bounce off it. The broker retries with 1s, 2s,
# 4s and 8s of backoff and parks the message after five attempts, so its whole
# budget is roughly fifteen seconds — shorter than one turn carrying an intent
# call, a fallback model and a reply. Bouncing immediately would dead-letter the
# second message of a conversation while the first was still being analysed,
# which is precisely the "no reply at all" symptom this lock exists to remove.
LOCK_WAIT_MS = 15_000

_logger = get_logger(__name__)


class ConversationLock(Protocol):
    """Mutual exclusion over one conversation, for the span of one turn."""

    async def __aenter__(self) -> object: ...

    async def __aexit__(self, *exc_info: object) -> None: ...


# Declared here, by the consumer that needs it, and implemented by Redis in
# infrastructure: the dependency points inward, and a test can serialise turns
# without a Redis to talk to.
ConversationLockFactory = Callable[[str], ConversationLock]


def redis_lock_factory(settings: Settings) -> ConversationLockFactory:
    """The production lock.

    ``redis-py`` connects lazily, so building the client here costs nothing
    until the first turn arrives, and a Redis that is down surfaces as a
    retryable failure on that turn rather than as a worker that will not boot.

    The socket timeouts are not decoration. Without them a half-open connection
    makes ``SET`` wait for ever: ``handle`` never returns, its worker slot is
    never released, and the message is neither retried nor parked nor logged.
    Eight of those stall the consumer in silence.
    """
    redis = Redis.from_url(
        settings.redis_url,
        decode_responses=True,
        socket_connect_timeout=3,
        socket_timeout=3,
        health_check_interval=30,
    )
    return lambda name: RedisLock(
        redis, name=name, ttl_ms=LOCK_TTL_MS, wait_ms=LOCK_WAIT_MS
    )


class ConversationAnalysisConsumer(ConsumerBase):
    def __init__(
        self,
        spec: QueueSpec,
        broker: Broker,
        database: Database,
        settings: Settings,
        *,
        lock_factory: ConversationLockFactory | None = None,
    ) -> None:
        super().__init__(broker)
        self.queue_spec = spec
        self._database = database
        self._settings = settings
        self._lock_factory = lock_factory or redis_lock_factory(settings)

    async def handle(self, payload: dict[str, Any]) -> None:
        message_id, session_id = parse_analysis_payload(payload)
        # One turn at a time per conversation. Two messages sent seconds apart
        # otherwise race on the session's optimistic lock, and the loser rolls
        # back a transaction that had already staged the user's reply — which is
        # how a message ends up with no answer while the next one gets two.
        async with AsyncExitStack() as stack:
            try:
                # Only the acquisition is guarded. Wrapping the whole turn would
                # relabel any future lock taken inside the use case as
                # contention on this conversation, and retry it on that basis.
                await stack.enter_async_context(
                    self._lock_factory(f"conversation:{session_id}")
                )
            except LockBackendUnavailable as exc:
                # Distinct from contention on purpose: an outage hits every
                # conversation at once, and ``_handle_failure`` records only
                # ``str(exc)`` — so the cause has to be in this message or the
                # DLQ header explains nothing.
                _logger.warning(
                    "lock_backend_unavailable",
                    session_id=str(session_id),
                    error=str(exc),
                )
                raise RetryableHandlerError(
                    f"the conversation lock backend is unavailable: {exc}"
                ) from exc
            except NotAcquired as exc:
                # Retryable, never permanent: this turn still owes the user an
                # answer, and the worker holding the conversation is about to
                # finish.
                _logger.info("lock_contended", session_id=str(session_id))
                raise RetryableHandlerError(
                    f"conversation {session_id} is still being analysed"
                ) from exc
            report = await self._analyse(message_id, session_id)
        _logger.info(
            "conversation_message_analysed",
            message_id=str(report.message_id),
            session_id=str(report.session_id),
            intent=report.intent,
            confidence=report.confidence,
            used_llm=report.used_llm,
            accepted=report.accepted,
            reason=report.reason,
            next_step=report.next_step,
            question_key=report.question_key,
            rejected_facts=sorted(report.rejected_facts),
            cost_usd=str(report.cost_usd),
        )

    async def _analyse(
        self, message_id: uuid.UUID, session_id: uuid.UUID
    ) -> AnalysisReport:
        """The turn itself, inside its own transaction.

        Split out so ``handle`` reads as what it is — serialise, analyse, log —
        and so the transaction's lifetime is visibly nested inside the lock's.
        """
        async with unit_of_work(self._database) as uow:
            return await analyse_conversation_message_use_case(
                uow, settings=self._settings
            ).execute(
                AnalysisCommand(message_id=message_id, session_id=session_id),
                now=datetime.now(UTC),
            )


def parse_analysis_payload(payload: dict[str, Any]) -> tuple[uuid.UUID, uuid.UUID]:
    """Extract the ids, or refuse the message permanently."""
    body = payload.get("payload")
    if not isinstance(body, dict):
        raise PermanentHandlerError("analysis payload is missing or not an object")
    metadata = body.get("metadata")
    if not isinstance(metadata, dict):
        raise PermanentHandlerError("analysis payload has no metadata")

    def require(name: str) -> uuid.UUID:
        value = metadata.get(name)
        if not isinstance(value, str) or not value.strip():
            raise PermanentHandlerError(f"analysis field '{name}' is missing")
        try:
            return uuid.UUID(value)
        except ValueError as exc:
            raise PermanentHandlerError(
                f"analysis field '{name}' is not a uuid: {value}"
            ) from exc

    return require("message_id"), require("session_id")
