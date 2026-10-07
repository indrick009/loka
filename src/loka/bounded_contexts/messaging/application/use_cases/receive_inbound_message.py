"""Ingestion of inbound WhatsApp messages.

This is the platform's front door: every conversation a tenant or landlord ever
has passes through here. Two properties matter more than anything else in this
file.

**Idempotence.** Gateways redeliver. A WhatsApp network retry, a consumer crash
after the ack was lost, or two workers racing on the same queue can each deliver
the same message twice, and a duplicated "je veux louer" must not create two
applications or two viewings. De-duplication is therefore owned by the database
(``uq_inbound_external_message``) and this use case returns early instead of
trying to be clever about it.

**No side effect before the dedupe check.** The conversation session is created
only after the message is known to be new, so a redelivery cannot resurrect a
session for a contact who has long since stopped writing.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import Any

from loka.bounded_contexts.identity.application.ports import UserProvisioning
from loka.bounded_contexts.identity.domain.value_objects.phone_number import PhoneNumber
from loka.bounded_contexts.messaging.domain.entities.conversation_session import (
    ConversationSession,
    FlowName,
)
from loka.bounded_contexts.messaging.domain.events.message_events import (
    ConversationAnalysisRequested,
    WhatsAppMessageIngested,
)
from loka.bounded_contexts.messaging.domain.repositories.conversation_session_repository import (
    ConversationSessionRepository,
)
from loka.bounded_contexts.messaging.domain.repositories.inbound_message_repository import (
    InboundMessageRepository,
    StoredInboundMessage,
)
from loka.bounded_contexts.messaging.domain.value_objects.inbound_message import (
    InboundMessage,
    MessageKind,
)
from loka.shared.application.context import current_context
from loka.shared.application.unit_of_work import UnitOfWork
from loka.shared.domain.clock import ensure_utc

# A gateway timestamp is untrusted input used to order a conversation. Without
# a bound, one clock-skewed or spoofed message would sort forever at one end of
# ix_inbound_conversation_time and hide the rest of the thread.
MAX_PROVIDER_TIMESTAMP_DRIFT = timedelta(days=7)


@dataclass(frozen=True, slots=True)
class ReceiveInboundMessageCommand:
    provider: str
    external_message_id: str
    conversation_ref: str
    sender_phone: str
    message_kind: str
    provider_timestamp: datetime
    text: str | None = None
    media_object_key: str | None = None
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True, slots=True)
class InboundMessageReceipt:
    message_id: uuid.UUID
    session_id: uuid.UUID | None
    sender_user_id: uuid.UUID | None
    duplicated: bool


class ReceiveInboundMessageUseCase:
    name = "receive_inbound_message"

    def __init__(
        self,
        uow: UnitOfWork,
        *,
        inbound: InboundMessageRepository,
        sessions: ConversationSessionRepository,
        users: UserProvisioning,
    ) -> None:
        self._uow = uow
        self._inbound = inbound
        self._sessions = sessions
        self._users = users

    async def execute(
        self, command: ReceiveInboundMessageCommand, *, now: datetime
    ) -> InboundMessageReceipt:
        received_at = ensure_utc(now)
        phone = PhoneNumber.normalize(command.sender_phone)
        kind = _resolve_kind(command.message_kind)

        existing = await self._inbound.get_by_external_id(
            provider=command.provider, external_message_id=command.external_message_id
        )
        if existing is not None:
            return self._receipt(existing, duplicated=True)

        message = InboundMessage(
            message_id=uuid.uuid4(),
            provider=command.provider,
            external_message_id=command.external_message_id,
            conversation_ref=command.conversation_ref,
            sender_phone=phone,
            kind=kind,
            provider_timestamp=_bounded_timestamp(command.provider_timestamp, received_at),
            received_at=received_at,
            text=command.text,
            media_object_key=command.media_object_key,
            metadata={
                **command.metadata,
                "raw_kind": command.message_kind,
                "raw_provider_timestamp": command.provider_timestamp.isoformat(),
            },
        )

        stored = await self._inbound.add_if_absent(message)
        if stored is None:
            # Another worker inserted the same message between the check above
            # and this insert. Its row wins; this delivery is a no-op.
            raced = await self._inbound.get_by_external_id(
                provider=message.provider, external_message_id=message.external_message_id
            )
            if raced is None:  # pragma: no cover - the row cannot disappear
                raise RuntimeError("inbound message vanished between insert and re-read")
            return self._receipt(raced, duplicated=True)

        # First contact auto-registers: a WhatsApp message proves the sender
        # holds the number, so no separate sign-up is needed. The account is
        # created in this same transaction, after the dedupe check, so a
        # redelivery cannot mint a second one.
        user_id = await self._users.ensure_user(phone, now=received_at)
        session = await self._resolve_session(phone, user_id=user_id, now=received_at)

        await self._inbound.attach_conversation(
            stored.message_id, session_id=session.id, sender_user_id=user_id
        )

        self._uow.events.stage(
            [
                WhatsAppMessageIngested(
                    aggregate_type="InboundMessage",
                    aggregate_id=stored.message_id,
                    aggregate_version=1,
                    occurred_at=received_at,
                    message_id=stored.message_id,
                    sender_user_id=user_id,
                    session_id=session.id,
                    masked_phone=phone.masked(),
                    conversation_ref=message.conversation_ref,
                    provider=message.provider,
                    message_kind=kind.value,
                    has_text=message.has_text,
                    has_media=message.has_media,
                    text_length=len(message.text or ""),
                ),
                # Staged in the same transaction: a durable message nobody
                # analyses is a bug no retry can repair, whereas an event for a
                # message that rolled back simply never appears.
                ConversationAnalysisRequested(
                    aggregate_type="InboundMessage",
                    aggregate_id=stored.message_id,
                    aggregate_version=1,
                    occurred_at=received_at,
                    message_id=stored.message_id,
                    session_id=session.id,
                    sender_user_id=user_id,
                    provider=message.provider,
                    message_kind=kind.value,
                ),
            ],
            correlation_id=current_context().get("correlation_id") or "unknown",
        )
        await self._uow.commit()
        return InboundMessageReceipt(
            message_id=stored.message_id,
            session_id=session.id,
            sender_user_id=user_id,
            duplicated=False,
        )

    async def _resolve_session(
        self, phone: PhoneNumber, *, user_id: uuid.UUID, now: datetime
    ) -> ConversationSession:
        """Get the live conversation for this number, or open a new one.

        The revision observed on load is passed to ``save`` so a concurrent
        worker that advanced the same session first wins instead of silently
        overwriting its step.
        """
        session = await self._sessions.latest_by_phone(phone)
        if session is None:
            created = ConversationSession(
                session_id=uuid.uuid4(),
                user_id=user_id,
                phone=phone,
                now=now,
                flow=FlowName.SUPPORT,
            )
            await self._sessions.add(created)
            return created

        observed_revision = session.revision
        changed = False
        if session.user_id != user_id:
            # Same number, account recreated or merged: the account is the
            # authority, the session follows it.
            session.user_id = user_id
            changed = True
        if session.is_idle(now):
            # A new conversation after the idle window: same durable session,
            # cleared state, so history is not silently concatenated.
            session.restart(now=now)
            changed = True
        if changed:
            await self._sessions.save(session, expected_revision=observed_revision)
        return session

    @staticmethod
    def _receipt(
        stored: StoredInboundMessage, *, duplicated: bool
    ) -> InboundMessageReceipt:
        # session_id stays None when the stored row was never linked to a
        # conversation, which is a real state for a message inserted by an older
        # or crashed handler. Reporting a fabricated id would be worse.
        return InboundMessageReceipt(
            message_id=stored.message_id,
            session_id=stored.session_id,
            sender_user_id=stored.sender_user_id,
            duplicated=duplicated,
        )


def _resolve_kind(raw: str) -> MessageKind:
    """Map a gateway kind, never failing on an unknown one.

    A new provider format must not park the user's message in the DLQ: it is
    stored as UNSUPPORTED so the conversation can still answer in plain text,
    and the raw value is preserved in the message metadata.
    """
    try:
        return MessageKind(raw.upper())
    except ValueError:
        return MessageKind.UNSUPPORTED


def _bounded_timestamp(value: datetime, received_at: datetime) -> datetime:
    """Clamp an untrusted gateway timestamp to a plausible window."""
    timestamp = ensure_utc(value)
    drift = timestamp - received_at
    if abs(drift) <= MAX_PROVIDER_TIMESTAMP_DRIFT:
        return timestamp
    return (
        received_at + MAX_PROVIDER_TIMESTAMP_DRIFT
        if drift > timedelta(0)
        else received_at - MAX_PROVIDER_TIMESTAMP_DRIFT
    )
