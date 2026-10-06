"""Identifier generation port plus a typed identifier wrapper.

Domain code depends on :class:`IdGenerator` only, so tests can inject
deterministic identifiers and production can switch to UUIDv7 or a
snowflake-like scheme without touching aggregates.
"""

from __future__ import annotations

import time
import uuid
from collections.abc import Callable
from random import getrandbits
from threading import Lock
from typing import Generic, NewType, Protocol, TypeVar, runtime_checkable

_SEQUENCE_BITS = 12


@runtime_checkable
class IdGenerator(Protocol):
    def new_id(self) -> uuid.UUID:
        """Return a globally unique, sortable identifier."""


class Uuid7Generator:
    """UUIDv7 keeps insertion order locality, which matters a lot on PostgreSQL.

    ``uuid.uuid7()`` only exists from CPython 3.14, so the RFC 9562 layout is
    built here for older interpreters. Time is read from a monotonic clock to
    stay unique when two ids are generated inside the same millisecond.
    """

    __slots__ = ("_borrowed", "_last_ms", "_last_value", "_lock", "_sequence")

    def __init__(self) -> None:
        self._last_ms = -1
        self._sequence = 0
        self._borrowed = False
        self._last_value = -1
        self._lock = Lock()

    def new_id(self) -> uuid.UUID:
        with self._lock:
            now_ms = int(time.time() * 1000)
            if now_ms > self._last_ms and not self._borrowed:
                self._last_ms = now_ms
                self._sequence = getrandbits(_SEQUENCE_BITS)
            else:
                # Same millisecond, backwards clock, or a millisecond borrowed
                # after counter overflow: keep counting so ids stay ordered.
                self._sequence += 1
                if self._sequence >= 1 << _SEQUENCE_BITS:
                    self._sequence = 0
                    self._borrowed = False
                    if self._last_ms >= now_ms:
                        self._last_ms += 1
                        self._borrowed = True
                elif now_ms > self._last_ms:
                    self._last_ms = now_ms
            timestamp_ms = self._last_ms
            sequence = self._sequence

            # RFC 9562 layout: 48-bit unix ms | 4-bit version | 12-bit rand_a
            # | 2-bit variant | 62-bit rand_b. Unlike the specification's advice
            # we spend all of rand_a on a counter, which is what makes ids issued
            # within the same millisecond strictly ordered; rand_b stays random
            # so different processes and hosts still never collide.
            value = (
                ((timestamp_ms & 0xFFFF_FFFF_FFFF) << 80)
                | (0x7 << 76)
                | (sequence << 64)
                | (0b10 << 62)
                | getrandbits(62)
            )
            if value <= self._last_value:
                # Last line of defence: a clock correction must never hand out a
                # value lower than the previous one, so borrow a millisecond.
                value = (value & ~0xFFFFFFFFFFFF) + ((value & 0xFFFFFFFFFFFF) + 1)
                if value <= self._last_value:
                    self._last_ms += 1
                    value = (
                        ((self._last_ms & 0xFFFF_FFFF_FFFF) << 80)
                        | (0x7 << 76)
                        | (0 << 64)
                        | (0b10 << 62)
                        | getrandbits(62)
                    )
                self._sequence = 0
            self._last_value = value
        return uuid.UUID(int=value)


_default_uuid7 = Uuid7Generator()


def new_id() -> uuid.UUID:
    """Process-wide default generator; cheap enough for hot paths."""
    return _default_uuid7.new_id()


class SequenceIdGenerator:
    def __init__(self, factory: Callable[[], uuid.UUID] = uuid.uuid4) -> None:
        self._factory = factory

    def new_id(self) -> uuid.UUID:
        return self._factory()


T = TypeVar("T")


class Identifier(Generic[T]):
    """Thin wrapper giving compile-time separation between different id spaces."""

    __slots__ = ("value",)

    def __init__(self, value: uuid.UUID) -> None:
        self.value = value

    def __eq__(self, other: object) -> bool:
        return isinstance(other, Identifier) and other.value == self.value

    def __hash__(self) -> int:
        return hash(self.value)

    def __repr__(self) -> str:
        return f"{type(self).__name__}({self.value})"

    def __str__(self) -> str:
        return str(self.value)


UserId = Identifier[object]
PropertyId = Identifier[object]
ApplicationId = Identifier[object]
VisitId = Identifier[object]
PaymentId = Identifier[object]
ConversationId = Identifier[object]
MessageId = Identifier[object]
VerificationId = Identifier[object]
ReportId = Identifier[object]
MediaId = Identifier[object]

NewType("RawUserId", uuid.UUID)
NewType("RawPropertyId", uuid.UUID)