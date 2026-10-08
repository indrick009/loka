"""Ports the Visit context depends on."""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from typing import Protocol, runtime_checkable


@dataclass(frozen=True, slots=True)
class VisitProperty:
    property_id: uuid.UUID
    landlord_id: uuid.UUID


@runtime_checkable
class VisitPropertyDirectory(Protocol):
    async def lookup(self, property_id: uuid.UUID) -> VisitProperty | None: ...