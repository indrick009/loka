"""Entity base class.

Entities carry identity: two entities with the same id are equal even when
their attributes differ. Mutable state changes go through explicit
behaviour methods so invariants cannot be bypassed from the outside.
"""

from __future__ import annotations

import uuid
from typing import Any

from loka.shared.domain.errors import InvariantViolation


class Entity:
    __slots__ = ("_id",)

    @property
    def id(self) -> uuid.UUID:
        return self._id

    def _assign_id(self, entity_id: uuid.UUID) -> None:
        self._id = entity_id

    def __eq__(self, other: object) -> bool:
        if other.__class__ is not self.__class__:
            return NotImplemented
        return self._id == other._id

    def __hash__(self) -> int:
        return hash((self.__class__, self._id))

    def __repr__(self) -> str:
        return f"{type(self).__name__}(id={self._id})"

    def _reject(self, reason: str, **context: Any) -> None:
        raise InvariantViolation(f"{type(self).__name__}: {reason}", context=context)