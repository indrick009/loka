"""Two workers, one conversation: the loser waits, it does not lose the turn.

The analysis worker runs eight messages at a time with no ordering per session.
Two messages from the same person arriving together therefore race on the
session's optimistic lock: one commits, the other raises ``ConcurrencyConflict``
and its whole transaction rolls back — including the reply it had staged. The
broker retries it, so the answer either arrives out of order or, once the retry
budget is spent, never arrives at all. Observed in production as one message
with no answer followed by another with two.

See ``docs/conversation-defects-v1.md`` (D2).

The fix is to serialise on the conversation before any work starts, so the
conflict cannot happen at all. What these cases lock down is the behaviour at
the seam: a conversation already being processed must come back through the
retry path, and the lock must be narrow enough that two different conversations
never wait on each other.
"""

from __future__ import annotations

import uuid
from typing import Any

import pytest

from loka.interfaces.worker.conversation_analysis_consumer import (
    AI_REQUEST_QUEUE,
    ConversationAnalysisConsumer,
)
from loka.shared.infrastructure.broker.consumer import RetryableHandlerError
from loka.shared.infrastructure.broker.topology import QUEUES, Broker
from loka.shared.infrastructure.config.settings import Settings
from loka.shared.infrastructure.redis_client import (
    LockBackendUnavailable,
    NotAcquired,
)

pytestmark = pytest.mark.unit


class _HeldLock:
    """The lock another worker already owns."""

    error: type[NotAcquired] = NotAcquired
    message = "another worker holds this conversation"

    async def __aenter__(self) -> _HeldLock:
        raise self.error(self.message)

    async def __aexit__(self, *exc_info: object) -> None:
        return None


class _UnreachableBackend(_HeldLock):
    """Redis itself is down, which is a different problem entirely."""

    error = LockBackendUnavailable
    message = "lock backend unavailable: connection refused"


class _LockFactory:
    def __init__(self, lock: type[_HeldLock] = _HeldLock) -> None:
        self.names: list[str] = []
        self._lock = lock

    def __call__(self, name: str) -> _HeldLock:
        self.names.append(name)
        return self._lock()


class _UntouchableDatabase:
    """Any access proves work began before the conversation was serialised."""

    @property
    def sessions(self) -> Any:  # pragma: no cover - raising is the assertion
        raise AssertionError(
            "the analysis opened a transaction before taking the conversation lock"
        )


def analysis_request(session_id: uuid.UUID) -> dict[str, Any]:
    return {
        "payload": {
            "metadata": {
                "message_id": str(uuid.uuid4()),
                "session_id": str(session_id),
            }
        }
    }


def build_with(
    settings: Settings, lock: type[_HeldLock]
) -> tuple[ConversationAnalysisConsumer, _LockFactory]:
    factory = _LockFactory(lock)
    return build(settings, factory), factory


def build(settings: Settings, factory: _LockFactory) -> ConversationAnalysisConsumer:
    spec = next(spec for spec in QUEUES if spec.name == AI_REQUEST_QUEUE)
    return ConversationAnalysisConsumer(
        spec,
        Broker(settings.broker),
        _UntouchableDatabase(),  # type: ignore[arg-type]
        settings,
        lock_factory=factory,
    )


class TestSerialisation:
    async def test_a_conversation_held_elsewhere_is_retried_not_dropped(
        self, settings: Settings
    ) -> None:
        """Retryable, not permanent: the turn still owes the user an answer."""
        consumer = build(settings, _LockFactory())

        with pytest.raises(RetryableHandlerError):
            await consumer.handle(analysis_request(uuid.uuid4()))

    async def test_nothing_is_read_or_written_before_the_lock_is_held(
        self, settings: Settings
    ) -> None:
        """``_UntouchableDatabase`` turns a premature transaction into a failure.

        Taking the lock after opening the unit of work would reintroduce exactly
        the race this guard exists to remove.
        """
        consumer = build(settings, _LockFactory())

        with pytest.raises(RetryableHandlerError):
            await consumer.handle(analysis_request(uuid.uuid4()))

    async def test_the_lock_is_scoped_to_one_conversation(
        self, settings: Settings
    ) -> None:
        """A global lock would serialise the whole platform onto one conversation."""
        factory = _LockFactory()
        consumer = build(settings, factory)
        first, second = uuid.uuid4(), uuid.uuid4()

        for session_id in (first, second):
            with pytest.raises(RetryableHandlerError):
                await consumer.handle(analysis_request(session_id))

        assert str(first) in factory.names[0]
        assert str(second) in factory.names[1]
        assert factory.names[0] != factory.names[1]


class TestContentionAndOutageAreToldApart:
    """A held lock and a dead Redis both used to say "already being analysed".

    An operator reading that during a Redis outage goes looking for a stuck
    worker, while every analysis message dead-letters after about fifteen
    seconds of retries. Both are retryable, but they are not the same incident.
    """

    async def test_a_dead_lock_backend_is_still_retryable(
        self, settings: Settings
    ) -> None:
        consumer, _ = build_with(settings, _UnreachableBackend)

        with pytest.raises(RetryableHandlerError) as raised:
            await consumer.handle(analysis_request(uuid.uuid4()))

        assert "unavailable" in str(raised.value).lower()

    async def test_contention_does_not_claim_the_backend_is_down(
        self, settings: Settings
    ) -> None:
        consumer, _ = build_with(settings, _HeldLock)

        with pytest.raises(RetryableHandlerError) as raised:
            await consumer.handle(analysis_request(uuid.uuid4()))

        assert "unavailable" not in str(raised.value).lower()

    async def test_the_cause_is_preserved_for_the_logs(
        self, settings: Settings
    ) -> None:
        """``_handle_failure`` records ``str(exc)`` only, so the chain must carry
        the cause or the DLQ header explains nothing."""
        consumer, _ = build_with(settings, _UnreachableBackend)

        with pytest.raises(RetryableHandlerError) as raised:
            await consumer.handle(analysis_request(uuid.uuid4()))

        assert isinstance(raised.value.__cause__, LockBackendUnavailable)
