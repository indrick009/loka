"""Inbound ingestion: de-duplication, session handling, event emission."""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any

import pytest

from loka.bounded_contexts.identity.domain.value_objects.phone_number import PhoneNumber
from loka.bounded_contexts.messaging.application.use_cases.receive_inbound_message import (
    ReceiveInboundMessageCommand,
    ReceiveInboundMessageUseCase,
)
from loka.bounded_contexts.messaging.domain.entities.conversation_session import (
    SESSION_IDLE_TIMEOUT,
    ConversationSession,
    FlowName,
    FlowStep,
)
from loka.bounded_contexts.messaging.domain.repositories.inbound_message_repository import (
    StoredInboundMessage,
)
from loka.bounded_contexts.messaging.domain.value_objects.inbound_message import (
    InboundMessage,
    MessageKind,
)
from loka.shared.application.unit_of_work import UnitOfWork
from loka.shared.domain.errors import InvariantViolation, ValidationFailed

NOW = datetime(2026, 3, 1, 9, 0, tzinfo=UTC)
PHONE = "+237699123456"


class FakeInboundRepository:
    """Mimics the ON CONFLICT semantics of the real adapter."""

    def __init__(self) -> None:
        self.rows: dict[tuple[str, str], StoredInboundMessage] = {}
        self.messages: list[InboundMessage] = []

    async def get_by_external_id(
        self, *, provider: str, external_message_id: str
    ) -> StoredInboundMessage | None:
        return self.rows.get((provider, external_message_id))

    async def add_if_absent(self, message: InboundMessage) -> StoredInboundMessage | None:
        key = (message.provider, message.external_message_id)
        if key in self.rows:
            return None
        stored = StoredInboundMessage(
            message_id=message.message_id,
            session_id=None,
            sender_user_id=None,
            processed=False,
        )
        self.rows[key] = stored
        self.messages.append(message)
        return stored

    async def attach_conversation(
        self,
        message_id: uuid.UUID,
        *,
        session_id: uuid.UUID | None,
        sender_user_id: uuid.UUID | None,
    ) -> None:
        for key, row in self.rows.items():
            if row.message_id == message_id:
                self.rows[key] = StoredInboundMessage(
                    message_id=row.message_id,
                    session_id=session_id,
                    sender_user_id=sender_user_id,
                    processed=row.processed,
                )


class FakeSessionRepository:
    def __init__(self) -> None:
        self.store: dict[str, ConversationSession] = {}
        self.saved: list[tuple[uuid.UUID, int | None]] = []
        self.added: list[ConversationSession] = []

    async def latest_by_phone(self, phone: PhoneNumber) -> ConversationSession | None:
        return self.store.get(phone.e164)

    async def add(self, session: ConversationSession) -> None:
        self.store[session.phone.e164] = session
        self.added.append(session)

    async def save(
        self, session: ConversationSession, *, expected_revision: int | None = None
    ) -> None:
        self.saved.append((session.id, expected_revision))
        self.store[session.phone.e164] = session


class FakeUserDirectory:
    def __init__(self, user_id: uuid.UUID | None) -> None:
        self._user_id = user_id
        self.calls = 0

    async def user_id_for_phone(self, phone: PhoneNumber) -> uuid.UUID | None:
        self.calls += 1
        return self._user_id


class RecordingPublisher:
    def __init__(self) -> None:
        self.staged: list[Any] = []
        self.correlation_ids: list[str] = []

    def stage(self, events: Any, *, correlation_id: str) -> None:
        self.staged.extend(events)
        self.correlation_ids.append(correlation_id)

    async def flush(self) -> None:
        return None


class FakeUnitOfWork(UnitOfWork):
    def __init__(
        self,
        inbound: FakeInboundRepository,
        sessions: FakeSessionRepository,
    ) -> None:
        self.inbound = inbound
        self.sessions = sessions
        self.publisher = RecordingPublisher()
        self.commits = 0
        self.rollbacks = 0

    def __enter__(self) -> FakeUnitOfWork:
        return self

    def __exit__(self, *args: object) -> None:
        return None

    async def __aenter__(self) -> FakeUnitOfWork:
        return self

    async def __aexit__(self, *args: object) -> None:
        return None

    async def commit(self) -> None:
        self.commits += 1

    async def rollback(self) -> None:
        self.rollbacks += 1

    @property
    def events(self) -> RecordingPublisher:
        return self.publisher

    def collect(self, aggregate: object) -> None:
        return None

    def repository(self, name: str) -> object:
        return {"inbound_messages": self.inbound, "conversation_sessions": self.sessions}[name]


@dataclass(slots=True)
class _Harness:
    use_case: ReceiveInboundMessageUseCase
    uow: FakeUnitOfWork
    users: FakeUserDirectory
    inbound: FakeInboundRepository
    sessions: FakeSessionRepository


def _harness(*, user_id: uuid.UUID | None = None) -> _Harness:
    inbound = FakeInboundRepository()
    sessions = FakeSessionRepository()
    uow = FakeUnitOfWork(inbound, sessions)
    users = FakeUserDirectory(user_id)
    return _Harness(
        use_case=ReceiveInboundMessageUseCase(
            uow, inbound=inbound, sessions=sessions, users=users
        ),
        uow=uow,
        users=users,
        inbound=inbound,
        sessions=sessions,
    )


def _command(**overrides: Any) -> ReceiveInboundMessageCommand:
    payload: dict[str, Any] = {
        "provider": "baileys",
        "external_message_id": "BAIL_E1_ABC",
        "conversation_ref": "699123456@s.whatsapp.net",
        "sender_phone": "699 12 34 56",
        "message_kind": "TEXT",
        "provider_timestamp": NOW,
        "text": "Je veux louer un appartement",
    }
    payload.update(overrides)
    return ReceiveInboundMessageCommand(**payload)


async def test_a_new_message_is_stored_and_reported() -> None:
    h = _harness(user_id=uuid.uuid4())

    receipt = await h.use_case.execute(_command(), now=NOW)

    assert receipt.duplicated is False
    assert receipt.session_id is not None
    assert receipt.sender_user_id is not None
    assert h.uow.commits == 1
    # Both events stage in the same transaction: a stored message nobody
    # analyses is unrecoverable, so the two can never be committed apart.
    assert [event.event_type for event in h.uow.publisher.staged] == [
        "WhatsAppMessageIngested",
        "ConversationAnalysisRequested",
    ]


async def test_the_analysis_request_carries_no_message_text() -> None:
    """The AI context re-reads the message through its repository, so no queue
    ever holds a copy of what a landlord typed."""
    h = _harness(user_id=uuid.uuid4())

    await h.use_case.execute(_command(text="mon CNI est 123456789"), now=NOW)

    analysis = h.uow.publisher.staged[-1]
    assert "mon CNI est" not in repr(analysis)
    assert analysis.message_id is not None
    assert analysis.session_id is not None


async def test_a_redelivered_message_is_ignored_without_side_effects() -> None:
    """The whole point: a gateway retry must not replay a command."""
    h = _harness(user_id=uuid.uuid4())
    first = await h.use_case.execute(_command(), now=NOW)

    second = await h.use_case.execute(_command(), now=NOW)

    assert second.duplicated is True
    assert second.message_id == first.message_id
    assert len(h.inbound.messages) == 1
    # No second event, no second session lookup, no second commit. The analysis
    # must not be requested twice for one message either.
    assert [event.event_type for event in h.uow.publisher.staged] == [
        "WhatsAppMessageIngested",
        "ConversationAnalysisRequested",
    ]
    assert h.users.calls == 1
    assert h.uow.commits == 1


async def test_an_unknown_sender_still_gets_a_session() -> None:
    """First contact is a normal state, not an error."""
    h = _harness(user_id=None)

    receipt = await h.use_case.execute(_command(), now=NOW)

    assert receipt.sender_user_id is None
    assert receipt.session_id is not None


async def test_the_first_message_opens_a_session() -> None:
    h = _harness(user_id=uuid.uuid4())

    receipt = await h.use_case.execute(_command(), now=NOW)

    session = h.sessions.store[PHONE]
    assert session.id == receipt.session_id
    assert session.user_id == receipt.sender_user_id
    assert session.flow is FlowName.SUPPORT
    assert session.step is FlowStep.START


async def test_a_follow_up_message_reuses_the_same_session() -> None:
    h = _harness(user_id=uuid.uuid4())
    first = await h.use_case.execute(_command(), now=NOW)

    second = await h.use_case.execute(
        _command(external_message_id="BAIL_E1_DEF", text="A Combien ?"),
        now=NOW + timedelta(minutes=2),
    )

    assert second.session_id == first.session_id
    # Reuse without mutation must not issue a write.
    assert h.sessions.saved == []


async def test_an_idle_session_is_restarted_instead_of_reused_forever() -> None:
    h = _harness(user_id=uuid.uuid4())
    first = await h.use_case.execute(_command(), now=NOW)
    session = h.sessions.store[PHONE]
    session.transition(FlowStep.COLLECT_PRICE, now=NOW)
    session.merge_context({"rent": 150_000}, now=NOW)
    await h.sessions.save(session, expected_revision=session.revision)

    later = NOW + SESSION_IDLE_TIMEOUT + timedelta(minutes=1)
    second = await h.use_case.execute(
        _command(external_message_id="BAIL_E1_LATE"), now=later
    )

    assert second.session_id == first.session_id
    reloaded = h.sessions.store[PHONE]
    assert reloaded.step is FlowStep.START
    assert reloaded.context == {}


async def test_a_session_that_moved_meanwhile_is_not_overwritten() -> None:
    """The revision observed on load is what guards the write."""
    h = _harness(user_id=uuid.uuid4())
    await h.use_case.execute(_command(), now=NOW)
    session = h.sessions.store[PHONE]
    session.transition(FlowStep.COLLECT_LOCATION, now=NOW)
    await h.sessions.save(session, expected_revision=session.revision - 1)
    revision_at_load = session.revision

    later = NOW + SESSION_IDLE_TIMEOUT + timedelta(minutes=1)
    before = len(h.sessions.saved)
    await h.use_case.execute(_command(external_message_id="BAIL_E1_LATE2"), now=later)

    # The use case guarded its write with the revision it read, not the one it
    # just produced: with a stale expectation the concurrent worker's step wins.
    writes_by_use_case = h.sessions.saved[before:]
    assert writes_by_use_case == [(session.id, revision_at_load)]


async def test_the_phone_number_is_normalised_before_it_is_stored() -> None:
    h = _harness(user_id=None)

    await h.use_case.execute(_command(sender_phone="+237 6 99 12 34 56"), now=NOW)

    assert h.inbound.messages[0].sender_phone.e164 == PHONE


async def test_an_unusable_phone_number_is_refused() -> None:
    h = _harness(user_id=None)

    with pytest.raises(InvariantViolation, match="phone number is empty"):
        await h.use_case.execute(_command(sender_phone=""), now=NOW)


async def test_a_message_with_neither_text_nor_media_is_refused() -> None:
    """Such a message could never be processed downstream."""
    h = _harness(user_id=None)

    with pytest.raises(ValidationFailed, match="text or a media object"):
        await h.use_case.execute(_command(text=None, message_kind="IMAGE"), now=NOW)


async def test_an_oversized_external_id_is_refused() -> None:
    """Otherwise it fails as a driver error and is retried for ever."""
    h = _harness(user_id=None)

    with pytest.raises(ValidationFailed, match="external_message_id"):
        await h.use_case.execute(_command(external_message_id="x" * 500), now=NOW)


async def test_an_unknown_message_kind_is_stored_as_unsupported() -> None:
    """A new provider format must still reach the conversation."""
    h = _harness(user_id=None)

    await h.use_case.execute(_command(message_kind="REACTION", text="👍"), now=NOW)

    stored = h.inbound.messages[0]
    assert stored.kind is MessageKind.UNSUPPORTED
    assert stored.metadata["raw_kind"] == "REACTION"


async def test_a_wild_provider_timestamp_is_clamped() -> None:
    """An untrusted clock must not park a message at one end of the thread."""
    h = _harness(user_id=None)
    skewed = NOW + timedelta(days=4000)

    await h.use_case.execute(_command(provider_timestamp=skewed), now=NOW)

    stored = h.inbound.messages[0]
    assert abs(stored.provider_timestamp - NOW) <= timedelta(days=7)
    assert stored.metadata["raw_provider_timestamp"] == skewed.isoformat()


async def test_the_published_event_never_carries_the_message_text() -> None:
    """CNIs and selfies travel in these messages; the bus must stay clean."""
    h = _harness(user_id=uuid.uuid4())

    await h.use_case.execute(_command(text="Mon CNI est 123456789"), now=NOW)

    event = h.uow.publisher.staged[0]
    serialised = str(event.to_payload())
    assert "123456789" not in serialised
    assert event.to_payload()["metadata"]["text_length"] == len("Mon CNI est 123456789")
    assert event.to_payload()["metadata"]["has_text"] is True
    assert event.to_payload()["metadata"]["masked_phone"].endswith("3456")


async def test_the_event_carries_the_resolved_conversation() -> None:
    user_id = uuid.uuid4()
    h = _harness(user_id=user_id)

    receipt = await h.use_case.execute(_command(), now=NOW)

    metadata = h.uow.publisher.staged[0].to_payload()["metadata"]
    assert metadata["message_id"] == str(receipt.message_id)
    assert metadata["session_id"] == str(receipt.session_id)
    assert metadata["sender_user_id"] == str(user_id)
    assert metadata["provider"] == "baileys"
    assert metadata["message_kind"] == "TEXT"
