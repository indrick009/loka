"""Messaging domain events.

Only facts downstream contexts are allowed to rely on travel on the bus. The
raw text of a WhatsApp message never leaves this module: it routinely holds a
CNI number, a selfie caption or a rent negotiation, and every consumer of this
event would end up storing a copy. Consumers get the shape of the message and
read the text through the messaging context when they truly need it.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from typing import Any

from loka.shared.domain.events import DomainEvent


@dataclass(frozen=True, slots=True)
class WhatsAppMessageIngested(DomainEvent):
    """A gateway message passed de-duplication and was durably stored.

    Named ``Ingested`` rather than ``Received`` on purpose: the routing key
    ``WhatsAppMessageReceived`` already identifies the raw gateway delivery on
    the ``whatsapp.incoming`` queue, and one event name must not mean both
    "something arrived on the wire" and "the platform accepted it".
    """

    event_type: str = "WhatsAppMessageIngested"
    message_id: uuid.UUID | None = None
    sender_user_id: uuid.UUID | None = None
    session_id: uuid.UUID | None = None
    masked_phone: str | None = None
    conversation_ref: str | None = None
    provider: str | None = None
    message_kind: str | None = None
    has_text: bool = False
    has_media: bool = False
    text_length: int = 0

    def to_payload(self) -> dict[str, Any]:
        """Keep the shared envelope shape, with the facts merged in metadata.

        The outbox serialises ``metadata`` for every event type, so the wire
        format stays identical to events recorded by aggregates.

        ``DomainEvent.to_payload(self)`` instead of ``super()``: a dataclass
        declared with ``slots=True`` is rebuilt after its body runs, which
        invalidates the implicit ``__class__`` cell a zero-argument ``super()``
        relies on. Calling the unbound base method is immune to that.
        """
        payload = DomainEvent.to_payload(self)
        payload["metadata"] = {
            **payload["metadata"],
            "message_id": str(self.message_id) if self.message_id else None,
            "sender_user_id": str(self.sender_user_id) if self.sender_user_id else None,
            "session_id": str(self.session_id) if self.session_id else None,
            "masked_phone": self.masked_phone,
            "conversation_ref": self.conversation_ref,
            "provider": self.provider,
            "message_kind": self.message_kind,
            "has_text": self.has_text,
            "has_media": self.has_media,
            "text_length": self.text_length,
        }
        return payload


@dataclass(frozen=True, slots=True)
class WhatsAppMessageSendRequested(DomainEvent):
    """Ask the outbound layer to answer one turn of a conversation.

    The analysis decides *what* is missing; the outbound layer owns *how to
    phrase it*. ``text`` optionally carries a model-written sentence composed
    while the analysis ran: when present the outbound layer sends it verbatim,
    and when absent it renders ``question_key`` from its own copy. That keeps
    the deterministic path (and every deployment without an AI key) working
    from stable keys, while still allowing a natural reply when a model is
    configured — without ever leaving the landlord waiting for a question
    nobody will send.

    The recipient is carried rather than resolved by the consumer: the session
    that owns the conversation is the only authority on who is being written to.
    """

    event_type: str = "WhatsAppMessageSendRequested"
    session_id: uuid.UUID | None = None
    recipient_phone: str | None = None
    reply_kind: str = "ACK"
    question_key: str | None = None
    text: str | None = None
    intent: str | None = None
    language: str = "fr"

    def to_payload(self) -> dict[str, Any]:
        payload = DomainEvent.to_payload(self)
        payload["metadata"] = {
            **payload["metadata"],
            "session_id": str(self.session_id) if self.session_id else None,
            "recipient_phone": self.recipient_phone,
            "reply_kind": self.reply_kind,
            "question_key": self.question_key,
            "text": self.text,
            "intent": self.intent,
            "language": self.language,
        }
        return payload


@dataclass(frozen=True, slots=True)
class ConversationAnalysisRequested(DomainEvent):
    """Ask the AI context to understand one stored message.

    Staged in the same transaction as :class:`WhatsAppMessageIngested`, which is
    the whole point: if the message is durable but this event were not, the
    platform would silently never understand it, and no retry could fix that.

    It carries identifiers only. The analysis reads the message back through
    the messaging repository, which keeps a single copy of the text and keeps
    it out of every queue.
    """

    event_type: str = "ConversationAnalysisRequested"
    message_id: uuid.UUID | None = None
    session_id: uuid.UUID | None = None
    sender_user_id: uuid.UUID | None = None
    provider: str | None = None
    message_kind: str | None = None

    def to_payload(self) -> dict[str, Any]:
        payload = DomainEvent.to_payload(self)
        payload["metadata"] = {
            **payload["metadata"],
            "message_id": str(self.message_id) if self.message_id else None,
            "session_id": str(self.session_id) if self.session_id else None,
            "sender_user_id": str(self.sender_user_id) if self.sender_user_id else None,
            "provider": self.provider,
            "message_kind": self.message_kind,
        }
        return payload
