"""Ports exposed by the Identity context to the other contexts."""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Protocol, runtime_checkable

from loka.bounded_contexts.identity.domain.value_objects.phone_number import PhoneNumber


@runtime_checkable
class UserProvisioning(Protocol):
    """Resolves the account behind a WhatsApp number, creating it on first contact.

    Consuming contexts depend on this port instead of the ``users`` table, so no
    context decides on its own what a phone number means and identity can move to
    another store without touching them. ``ensure_user`` subsumes the read: an
    unknown number is a normal state (first contact), not an error.
    """

    async def ensure_user(self, phone: PhoneNumber, *, now: datetime) -> uuid.UUID:
        """Return the account for ``phone``, creating it on first contact.

        First contact is a WhatsApp message, which proves the sender holds the
        number, so the account is active immediately and no OTP is sent.
        """