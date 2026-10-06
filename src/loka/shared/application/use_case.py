"""Use case base class.

Use cases orchestrate: load aggregate -> call behaviour -> persist -> collect
events. They contain no I/O detail and no transport concern.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Any, Generic, TypeVar

import structlog

from loka.shared.application.context import bind_logger
from loka.shared.application.unit_of_work import UnitOfWork
from loka.shared.domain.errors import DomainError
from loka.shared.domain.events import DomainEvent

Command = TypeVar("Command")
Result = TypeVar("Result")


class UseCase(ABC, Generic[Command, Result]):
    name: str = "use_case"

    def __init__(self, uow: UnitOfWork, logger: structlog.stdlib.BoundLogger | None = None) -> None:
        self._uow = uow
        self._logger = logger or structlog.get_logger(self.name)

    @abstractmethod
    async def execute(self, command: Command) -> Result: ...

    def _collect(self, aggregate: Any, events: list[DomainEvent]) -> None:
        self._uow.collect(aggregate)
        for event in events:
            self._logger.info(
                "domain_event_recorded",
                event_type=event.event_type,
                aggregate_type=event.aggregate_type,
                aggregate_id=str(event.aggregate_id),
                version=event.aggregate_version,
            )

    def _log(self, event: str, **fields: Any) -> None:
        bind_logger(self._logger, **fields).info(event)

    @staticmethod
    def _translate(exc: DomainError) -> DomainError:
        return exc