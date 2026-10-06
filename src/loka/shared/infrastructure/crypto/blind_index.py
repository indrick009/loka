"""Keyed blind index for lookups on columns that must stay encrypted.

``users.phone_e164`` is meant to become column-level encrypted, and a
randomised cipher cannot be searched. The schema therefore carries a
deterministic side value next to it. HMAC-SHA256 is the right primitive here:
unlike a bare SHA-256 it cannot be reversed with a rainbow table over the
~10^9 plausible Cameroonian numbers, so read-only database access does not
de-anonymise the contact list.

The key is derived from the same application secret as field encryption but
with its own salt and info label, so the two never share key material.
"""

from __future__ import annotations

import hmac
from hashlib import sha256

_SALT = b"loka.blind_index.v1"


def derive_blind_index_key(base_key: str) -> bytes:
    """Derive the index key from the application secret.

    Kept in one place because the value is persisted: regenerating it with a
    different salt or iteration count would orphan every stored index.
    """
    from cryptography.hazmat.primitives import hashes
    from cryptography.hazmat.primitives.kdf.pbkdf2 import PBKDF2HMAC

    kdf = PBKDF2HMAC(algorithm=hashes.SHA256(), length=32, salt=_SALT, iterations=200_000)
    return kdf.derive(base_key.encode())


def blind_index(value: str, *, key: bytes) -> str:
    """Return a deterministic, non-reversible lookup digest for ``value``."""
    return hmac.new(key, value.encode(), sha256).hexdigest()
