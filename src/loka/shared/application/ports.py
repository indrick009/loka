"""Ports shared by several bounded contexts."""

from __future__ import annotations

import hashlib
import hmac
from dataclasses import dataclass
from typing import Any, Protocol, runtime_checkable


@dataclass(frozen=True, slots=True)
class Cursor:
    """Opaque keyset pagination cursor; never expose raw offsets at scale."""

    value: str

    @staticmethod
    def encode(*parts: Any) -> Cursor:
        raw = "|".join(str(p) for p in parts)
        return Cursor(hashlib.sha256(raw.encode()).hexdigest()[:32])

    def decode_hint(self) -> str:
        return self.value[:8]


@runtime_checkable
class IdempotencyStore(Protocol):
    """Durable idempotency guard for externally-triggered operations."""

    async def acquire(self, key: str, *, ttl_seconds: int) -> bool: ...

    async def release(self, key: str) -> None: ...


@runtime_checkable
class SignatureVerifier(Protocol):
    """Verifies HMAC webhook signatures in constant time."""

    def verify(self, payload: bytes, signature: str, *, secret: str) -> bool: ...

    @staticmethod
    def sign(payload: bytes, secret: str) -> str:
        return hmac.new(secret.encode(), payload, hashlib.sha256).hexdigest()


class HmacSha256SignatureVerifier:
    def verify(self, payload: bytes, signature: str, *, secret: str) -> bool:
        expected = SignatureVerifier.sign(payload, secret)
        return hmac.compare_digest(expected, signature)


class InMemoryIdempotencyStore:
    """Test double; production uses Redis plus a DB unique constraint."""

    def __init__(self) -> None:
        self._keys: set[str] = set()

    async def acquire(self, key: str, *, ttl_seconds: int) -> bool:
        if key in self._keys:
            return False
        self._keys.add(key)
        return True

    async def release(self, key: str) -> None:
        self._keys.discard(key)