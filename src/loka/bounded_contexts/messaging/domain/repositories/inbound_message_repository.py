"""Messaging repository ports (domain-facing)."""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from typing import Protocol

from loka.bounded_contexts.messaging.domain.value_objects.inbound_message import InboundMessage


@dataclass(frozen=True, slots=True)
class StoredInboundMessage:
    """What is known about a message once it is in the table."""

    message_id: uuid.UUID
    session_id: uuid.UUID | None
    sender_user_id: uuid.UUID | None
    processed: bool
    # Only populated by `get_by_id`: the analysis stage needs the text back,
    # while the ingestion dedupe path deliberately never reads it.
    text: str | None = None


class InboundMessageRepository(Protocol):
    async def get_by_external_id(
        self, *, provider: str, external_message_id: str
    ) -> StoredInboundMessage | None:
        """Find a message the gateway already delivered under the same id."""

    async def get_by_id(self, message_id: uuid.UUID) -> StoredInboundMessage | None:
        """Load a message by primary key, with its text, for analysis."""

    async def add_if_absent(self, message: InboundMessage) -> StoredInboundMessage | None:
        """Insert unless ``(provider, external_message_id)`` already exists.

        Returns ``None`` when the row was already there. This must be enforced
        by the database rather than by a read-then-write: two workers consuming
        the same redelivered message in parallel both pass a pre-check.
        """

    async def attach_conversation(
        self,
        message_id: uuid.UUID,
        *,
        session_id: uuid.UUID | None,
        sender_user_id: uuid.UUID | None,
    ) -> None:
        """Link the stored message to its session and resolved account."""
