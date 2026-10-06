"""Conversation session repository port (domain-facing)."""

from __future__ import annotations

import uuid
from typing import Protocol

from loka.bounded_contexts.identity.domain.value_objects.phone_number import PhoneNumber
from loka.bounded_contexts.messaging.domain.entities.conversation_session import (
    ConversationSession,
)


class ConversationSessionRepository(Protocol):
    async def get_by_id(self, session_id: uuid.UUID) -> ConversationSession | None:
        """Load one session by primary key."""

    async def latest_by_phone(self, phone: PhoneNumber) -> ConversationSession | None:
        """Most recent session of this number, idle or not.

        The caller decides what to do with an idle session; this port only
        reports the durable state.
        """

    async def add(self, session: ConversationSession) -> None: ...

    async def save(
        self, session: ConversationSession, *, expected_revision: int | None = None
    ) -> None:
        """Raise ``ConcurrencyConflict`` when the revision no longer matches."""
