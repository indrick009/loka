"""Keyed blind index behaviour."""

from __future__ import annotations

from loka.shared.infrastructure.crypto.blind_index import (
    blind_index,
    derive_blind_index_key,
)

_KEY = derive_blind_index_key("test-secret")


def test_the_same_value_always_produces_the_same_index() -> None:
    assert blind_index("+237699123456", key=_KEY) == blind_index("+237699123456", key=_KEY)


def test_a_different_secret_produces_a_different_index() -> None:
    """Otherwise a stolen index from one environment could be replayed in another."""
    other = derive_blind_index_key("other-secret")
    assert blind_index("+237699123456", key=_KEY) != blind_index("+237699123456", key=other)


def test_the_index_does_not_leak_the_phone_number() -> None:
    index = blind_index("+237699123456", key=_KEY)
    assert "237" not in index
    assert "699123456" not in index
    assert len(index) == 64  # sha256 hex, matching users.phone_e164_hash


def test_the_index_key_is_not_the_field_encryption_key() -> None:
    """Field ciphertext and lookup index must not share key material."""
    from loka.shared.infrastructure.crypto.fields import AesGcmFieldCipher

    cipher = AesGcmFieldCipher("test-secret")
    assert cipher._derive_key("test-secret") != _KEY
