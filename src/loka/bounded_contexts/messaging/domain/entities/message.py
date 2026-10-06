"""Inbound and outbound message aggregates.

``external_message_id`` is the idempotency anchor: WhatsApp can deliver the
same event twice, and a unique constraint on this column is what stops a
duplicate application from being created.
"""

from __future__ import annotations

import uuid
from datetime import datetime
from enum import StrEnum

from loka.bounded_contexts.identity.domain.value_objects.phone_number import PhoneNumber
from loka.shared.domain.aggregate import AggregateRoot
from loka.shared.domain.clock import ensure_utc
from loka.shared.domain.errors import InvalidStateTransition


class MessageDirection(StrEnum):
    INBOUND = "INBOUND"
    OUTBOUND = "OUTBOUND"


class MessageKind(StrEnum):
    TEXT = "TEXT"
    IMAGE = "IMAGE"
    AUDIO = "AUDIO"
    VIDEO = "VIDEO"
    DOCUMENT = "DOCUMENT"
    LOCATION = "LOCATION"
    STICKER = "STICKER"
    CONTACT_CARD = "CONTACT_CARD"
    UNSUPPORTED = "UNSUPPORTED"


class DeliveryStatus(StrEnum):
    RECEIVED = "RECEIVED"
    QUEUED = "QUEUED"
    SENT = "SENT"
    DELIVERED = "DELIVERED"
    READ = "READ"
    FAILED = "FAILED"
    BLOCKED = "BLOCKED"


class InboundMessage(AggregateRoot):
    __slots__ = (
        "conversation_id",
        "external_message_id",
        "kind",
        "media_object_key",
        "message_id",
        "metadata",
        "normalized",
        "provider",
        "provider_timestamp",
        "received_at",
        "sender_phone",
        "sender_user_id",
        "session_id",
        "text",
    )

    def __init__(
        self,
        *,
        message_id: uuid.UUID,
        external_message_id: str,
        conversation_id: str,
        sender_phone: PhoneNumber,
        kind: MessageKind,
        now: datetime,
        text: str | None = None,
        media_object_key: str | None = None,
        sender_user_id: uuid.UUID | None = None,
        session_id: uuid.UUID | None = None,
        provider: str = "baileys",
        provider_timestamp: datetime | None = None,
        metadata: dict[str, object] | None = None,
    ) -> None:
        super().__init__(aggregate_type="InboundMessage")
        self._assign_id(message_id)
        if not external_message_id:
            self._reject("external_message_id is required for idempotency")
        if kind is not MessageKind.TEXT and not media_object_key and kind not in (
            MessageKind.LOCATION,
            MessageKind.CONTACT_CARD,
        ):
            self._reject("non-text message requires a media reference", kind=kind.value)
        if kind is MessageKind.TEXT and not (text or "").strip():
            self._reject("text message requires a body")

        self.message_id = message_id
        self.external_message_id = external_message_id
        self.conversation_id = conversation_id
        self.sender_phone = sender_phone
        self.sender_user_id = sender_user_id
        self.session_id = session_id
        self.kind = kind
        self.text = (text or "").strip() or None
        self.media_object_key = media_object_key
        self.provider = provider
        self.provider_timestamp = ensure_utc(provider_timestamp or now)
        self.received_at = ensure_utc(now)
        self.metadata = metadata or {}
        self.normalized = False

    @property
    def is_media(self) -> bool:
        return self.kind is not MessageKind.TEXT

    def mark_normalized(self) -> None:
        self.normalized = True

    def record_event(self, *, now: datetime, correlation_id: str) -> None:
        self.record(
            "WhatsAppMessageReceived",
            occurred_at=now,
            payload={
                "message_id": str(self.id),
                "external_message_id": self.external_message_id,
                "conversation_id": self.conversation_id,
                "sender_phone": self.sender_phone.masked(),
                "sender_user_id": str(self.sender_user_id) if self.sender_user_id else None,
                "session_id": str(self.session_id) if self.session_id else None,
                "message_type": self.kind.value,
                "text": self.text,
                "media_reference": self.media_object_key,
                "provider": self.provider,
                "provider_timestamp": self.provider_timestamp.isoformat(),
                "correlation_id": correlation_id,
            },
        )


class OutboundMessage(AggregateRoot):
    __slots__ = (
        "attempt_count",
        "conversation_id",
        "delivered_at",
        "failure_reason",
        "idempotency_key",
        "kind",
        "media_object_key",
        "message_id",
        "provider_message_id",
        "queued_at",
        "recipient_phone",
        "sent_at",
        "session_id",
        "status",
        "text",
    )

    def __init__(
        self,
        *,
        message_id: uuid.UUID,
        conversation_id: str,
        recipient_phone: PhoneNumber,
        text: str | None,
        now: datetime,
        kind: MessageKind = MessageKind.TEXT,
        media_object_key: str | None = None,
        session_id: uuid.UUID | None = None,
        idempotency_key: str | None = None,
    ) -> None:
        super().__init__(aggregate_type="OutboundMessage")
        self._assign_id(message_id)
        self.message_id = message_id
        self.conversation_id = conversation_id
        self.recipient_phone = recipient_phone
        self.session_id = session_id
        self.kind = kind
        self.text = text
        self.media_object_key = media_object_key
        self.status = DeliveryStatus.QUEUED
        self.idempotency_key = idempotency_key
        self.provider_message_id: str | None = None
        self.failure_reason: str | None = None
        self.attempt_count = 0
        self.queued_at = ensure_utc(now)
        self.sent_at: datetime | None = None
        self.delivered_at: datetime | None = None

    def mark_sent(self, *, provider_message_id: str, now: datetime) -> None:
        if self.status in (DeliveryStatus.DELIVERED, DeliveryStatus.READ):
            raise InvalidStateTransition("message already reached the recipient")
        self.status = DeliveryStatus.SENT
        self.provider_message_id = provider_message_id
        self.sent_at = ensure_utc(now)
        self.attempt_count += 1
        self._bump()

    def mark_delivered(self, *, now: datetime) -> None:
        if self.status is DeliveryStatus.FAILED:
            raise InvalidStateTransition("a failed message cannot be delivered")
        self.status = DeliveryStatus.DELIVERED
        self.delivered_at = ensure_utc(now)
        self._bump()

    def mark_read(self, *, now: datetime) -> None:
        self.status = DeliveryStatus.READ
        self._bump()

    def mark_failed(self, *, reason: str, now: datetime) -> None:
        self.status = DeliveryStatus.FAILED
        self.failure_reason = reason[:500]
        self.attempt_count += 1
        self._bump()

    def mark_blocked(self, *, reason: str, now: datetime) -> None:
        self.status = DeliveryStatus.BLOCKED
        self.failure_reason = reason[:500]
        self._bump()