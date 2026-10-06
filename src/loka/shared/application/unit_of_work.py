"""Unit of Work and EventPublisher ports.

The application layer only knows these protocols. Concrete adapters live in
``loka.shared.infrastructure`` and wrap SQLAlchemy / aio-pika.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from collections.abc import Callable, Iterable
from types import TracebackType
from typing import Any, Protocol, runtime_checkable

from loka.shared.domain.events import DomainEvent


@runtime_checkable
class EventPublisher(Protocol):
    """Stages domain events for reliable publication after commit."""

    def stage(self, events: Iterable[DomainEvent], *, correlation_id: str) -> None: ...

    async def flush(self) -> None: ...


@runtime_checkable
class SupportsAfterCommit(Protocol):
    """Lets a repository defer bookkeeping until the transaction is durable."""

    def after_commit(self, callback: Callable[[], None]) -> None: ...


class UnitOfWork(ABC):
    """Transaction boundary shared by all repositories of one use case.

    Implementations must guarantee that :meth:`commit` is atomic across every
    repository and that staged events are persisted in the same transaction.
    Callbacks registered through :class:`SupportsAfterCommit` run only once that
    transaction is committed, and are dropped on rollback.
    """

    @abstractmethod
    def __enter__(self) -> UnitOfWork: ...

    @abstractmethod
    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        tb: TracebackType | None,
    ) -> None: ...

    @abstractmethod
    async def __aenter__(self) -> UnitOfWork: ...

    @abstractmethod
    async def __aexit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        tb: TracebackType | None,
    ) -> None: ...

    @abstractmethod
    async def commit(self) -> None: ...

    @abstractmethod
    async def rollback(self) -> None: ...

    @property
    @abstractmethod
    def events(self) -> EventPublisher: ...

    @abstractmethod
    def collect(self, aggregate: Any) -> None:
        """Drain the aggregate's pending events into the publisher."""

    @abstractmethod
    def repository(self, name: str) -> Any:
        """Return the repository registered under ``name``.

        Repositories are resolved through the unit of work so a use case never
        builds infrastructure itself, and so every repository of one use case
        shares the same session and transaction.
        """


class Repository(Protocol):
    async def add(self, aggregate: Any) -> None: ...

    async def get(self, entity_id: Any) -> Any | None: ...

    async def save(self, aggregate: Any) -> None: ...