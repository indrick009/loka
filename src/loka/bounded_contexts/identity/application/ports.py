"""Ports exposed by the Identity context to the other contexts."""

from __future__ import annotations

import uuid
from typing import Protocol, runtime_checkable

from loka.bounded_contexts.identity.domain.value_objects.phone_number import PhoneNumber


@runtime_checkable
class UserDirectory(Protocol):
    """Read-only lookup of the account behind a WhatsApp number.

    Consuming contexts (Messaging, Rental, Fraud) depend on this port instead of
    the ``users`` table, so identity can move to another store without touching
    them, and so no context decides on its own what a phone number means.
    """

    async def user_id_for_phone(self, phone: PhoneNumber) -> uuid.UUID | None:
        """Return the account owning this number, if one exists.

        ``None`` means "unknown sender", never "no such user is allowed": an
        unrecognised number is a normal state (first contact) and must still be
        ingested.
        """
