"""Inbound WhatsApp message.

The gateway delivers whatever the network sent, so this object is where the
platform decides what a message is allowed to be. A row with neither text nor
media cannot be processed downstream, and an id the gateway may resend is the
only thing standing between a redelivery and a duplicated command, so both are
enforced here rather than in the handler.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from datetime import datetime
from enum import StrEnum
from typing import Any

from loka.bounded_contexts.identity.domain.value_objects.phone_number import PhoneNumber
from loka.shared.domain.errors import ValidationFailed

MAX_EXTERNAL_MESSAGE_ID = 180
MAX_CONVERSATION_REF = 96


class MessageKind(StrEnum):
    TEXT = "TEXT"
    IMAGE = "IMAGE"
    AUDIO = "AUDIO"
    VIDEO = "VIDEO"
    DOCUMENT = "DOCUMENT"
    STICKER = "STICKER"
    LOCATION = "LOCATION"
    CONTACT = "CONTACT"
    UNSUPPORTED = "UNSUPPORTED"


@dataclass(frozen=True, slots=True)
class InboundMessage:
    """One message received from a messaging gateway."""

    message_id: uuid.UUID
    provider: str
    external_message_id: str
    conversation_ref: str
    sender_phone: PhoneNumber
    kind: MessageKind
    provider_timestamp: datetime
    received_at: datetime
    text: str | None = None
    media_object_key: str | None = None
    metadata: dict[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        self._require_text(self.provider, "provider", 32)
        self._require_text(self.external_message_id, "external_message_id", MAX_EXTERNAL_MESSAGE_ID)
        self._require_text(self.conversation_ref, "conversation_ref", MAX_CONVERSATION_REF)
        if self.text is None and self.media_object_key is None:
            raise ValidationFailed(
                "an inbound message must carry text or a media object",
                context={
                    "provider": self.provider,
                    "external_message_id": self.external_message_id,
                    "kind": self.kind.value,
                },
            )

    @property
    def has_media(self) -> bool:
        return self.media_object_key is not None

    @property
    def has_text(self) -> bool:
        return bool(self.text)

    def _require_text(self, value: str, name: str, max_length: int) -> None:
        """Reject values the schema could not store.

        Length is validated here on purpose: an oversized id would otherwise
        surface as a driver error deep in the transaction and be retried for
        ever, since retrying a too-long string can never succeed.
        """
        if not value or not value.strip():
            raise ValidationFailed(f"{name} is required", context={"field": name})
        if len(value) > max_length:
            raise ValidationFailed(
                f"{name} exceeds {max_length} characters",
                context={"field": name, "length": len(value), "max_length": max_length},
            )
