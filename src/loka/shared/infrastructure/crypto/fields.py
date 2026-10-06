"""Secret field encryption for CNI numbers, selfies and phone numbers.

Uses AES-256-GCM with a random nonce. Ciphertext is stored base64 with a
version prefix so the algorithm can be rotated without data migration.
"""

from __future__ import annotations

import base64
import os
from typing import Protocol

from cryptography.hazmat.primitives.ciphers.aead import AESGCM

_PREFIX = "v1"


class Cipher(Protocol):
    def encrypt(self, plaintext: str, *, associated_data: str | None = None) -> str: ...

    def decrypt(self, ciphertext: str, *, associated_data: str | None = None) -> str: ...


class AesGcmFieldCipher:
    """AES-GCM with envelope key derived from the configured secret."""

    def __init__(self, base_key: str) -> None:
        self._cipher = AESGCM(self._derive_key(base_key))

    @staticmethod
    def _derive_key(base_key: str) -> bytes:
        from cryptography.hazmat.primitives import hashes
        from cryptography.hazmat.primitives.kdf.pbkdf2 import PBKDF2HMAC

        salt = b"loka.field.v1"
        kdf = PBKDF2HMAC(
            algorithm=hashes.SHA256(), length=32, salt=salt, iterations=200_000
        )
        return kdf.derive(base_key.encode())

    def encrypt(self, plaintext: str, *, associated_data: str | None = None) -> str:
        nonce = os.urandom(12)
        aad = (associated_data or "").encode()
        blob = self._cipher.encrypt(nonce, plaintext.encode(), aad)
        return f"{_PREFIX}:{base64.urlsafe_b64encode(nonce + blob).decode()}"

    def decrypt(self, ciphertext: str, *, associated_data: str | None = None) -> str:
        version, _, payload = ciphertext.partition(":")
        if version != _PREFIX:
            raise ValueError(f"unsupported ciphertext version: {version}")
        raw = base64.urlsafe_b64decode(payload.encode())
        nonce, blob = raw[:12], raw[12:]
        aad = (associated_data or "").encode()
        return self._cipher.decrypt(nonce, blob, aad).decode()


class NullCipher:
    """Development-only passthrough; refuses to run in production."""

    def __init__(self, *, is_production: bool) -> None:
        if is_production:
            raise RuntimeError("NullCipher is forbidden in production")

    def encrypt(self, plaintext: str, *, associated_data: str | None = None) -> str:
        return plaintext

    def decrypt(self, ciphertext: str, *, associated_data: str | None = None) -> str:
        return ciphertext