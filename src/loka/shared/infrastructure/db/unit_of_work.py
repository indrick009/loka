"""SQLAlchemy implementation of the Unit of Work.

One UoW instance per use case. It exposes exactly one session to every
repository and stages outbox rows so events commit atomically with state.
"""

from __future__ import annotations

from collections.abc import Callable, Iterable
from types import TracebackType
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from loka.shared.application.context import current_context
from loka.shared.application.unit_of_work import UnitOfWork
from loka.shared.domain.events import DomainEvent
from loka.shared.infrastructure.db.outbox import OutboxStore


class OutboxEventPublisher:
    """Buffers events until commit; nothing is sent inline."""

    def __init__(self, store: OutboxStore | None) -> None:
        self._store = store
        self._pending: list[DomainEvent] = []
        self._correlation_id: str | None = None

    def stage(self, events: Iterable[DomainEvent], *, correlation_id: str) -> None:
        self._pending.extend(events)
        # Remembered rather than discarded: the argument is the caller's
        # explicit intent and must win over the ambient context.
        self._correlation_id = correlation_id or self._correlation_id

    async def flush(self) -> None:
        if not self._pending or self._store is None:
            return
        correlation_id = (
            self._correlation_id
            or current_context().get("correlation_id")
            or "unknown"
        )
        await self._store.append(self._pending, correlation_id=correlation_id)
        self._pending.clear()
        self._correlation_id = None


class SqlAlchemyUnitOfWork(UnitOfWork):
    def __init__(
        self,
        sessions: async_sessionmaker[AsyncSession],
        *,
        repository_factories: dict[str, Any] | None = None,
    ) -> None:
        self._sessions = sessions
        self._session: AsyncSession | None = None
        self._outbox_store: OutboxStore | None = None
        self._publisher = OutboxEventPublisher(None)
        self._aggregate_events: list[DomainEvent] = []
        self._repositories: dict[str, Any] = {}
        self._repository_factories = repository_factories or {}
        self._after_commit: list[Callable[[], None]] = []
        self._entered = False

    @property
    def session(self) -> AsyncSession:
        if self._session is None:
            raise RuntimeError("UnitOfWork is not active")
        return self._session

    @property
    def events(self) -> OutboxEventPublisher:
        return self._publisher

    def collect(self, aggregate: Any) -> None:
        self._aggregate_events.extend(aggregate.pull_events())

    def repository(self, name: str) -> Any:
        if name not in self._repositories:
            factory = self._repository_factories.get(name)
            if factory is None:
                raise LookupError(f"repository '{name}' is not registered on this UnitOfWork")
            self._repositories[name] = factory(self)
        return self._repositories[name]

    def after_commit(self, callback: Callable[[], None]) -> None:
        """Queue bookkeeping to run once the transaction is durable.

        Repositories use this to move an aggregate's baseline version forward
        only once the write it guards cannot be rolled back.
        """
        if not self._entered:
            raise RuntimeError("after_commit() requires an active UnitOfWork")
        self._after_commit.append(callback)

    async def __aenter__(self) -> SqlAlchemyUnitOfWork:
        self._session = self._sessions()
        self._outbox_store = OutboxStore(self._session)
        self._publisher = OutboxEventPublisher(self._outbox_store)
        self._entered = True
        return self

    async def __aexit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        tb: TracebackType | None,
    ) -> None:
        if not self._entered or self._session is None:
            return
        try:
            if exc_type is not None:
                await self.rollback()
        finally:
            await self._session.close()
            self._entered = False
            self._session = None
            self._outbox_store = None
            self._repositories.clear()

    def __enter__(self) -> SqlAlchemyUnitOfWork:
        raise NotImplementedError("UnitOfWork is async-only")

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        tb: TracebackType | None,
    ) -> None:
        raise NotImplementedError

    async def commit(self) -> None:
        if self._aggregate_events:
            correlation_id = current_context().get("correlation_id") or "unknown"
            self._publisher.stage(self._aggregate_events, correlation_id=correlation_id)
            self._aggregate_events.clear()
        await self._publisher.flush()
        await self.session.commit()
        callbacks, self._after_commit = self._after_commit, []
        for callback in callbacks:
            callback()

    async def rollback(self) -> None:
        self._aggregate_events.clear()
        self._after_commit.clear()
        await self.session.rollback()