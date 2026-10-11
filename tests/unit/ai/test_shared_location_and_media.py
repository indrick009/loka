"""D3 / eval E3, at the stage where it is measured.

Two claims:

* a shared position produces a validated ``location`` **without a question
  about the city** — the pin already says it, so asking is the defect;
* an image produces a media attached to the listing, which is what Housing
  step 9 makes mandatory.

Neither path calls a model. A pin and a photo are things the user *sent*, not
proposals needing interpretation, so paying a model to look at them would be
both slower and less reliable than reading the fields.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any

import pytest

from loka.bounded_contexts.ai.application.ports import (
    ConversationTurn,
    TurnSummary,
    UsageEntry,
)
from loka.bounded_contexts.ai.application.use_cases.analyse_conversation_message import (
    AnalyseConversationMessageUseCase,
    AnalysisCommand,
)
from loka.bounded_contexts.identity.domain.value_objects.phone_number import PhoneNumber
from loka.bounded_contexts.messaging.domain.entities.conversation_session import (
    ConversationSession,
    FlowName,
    FlowStep,
)
from loka.bounded_contexts.messaging.domain.repositories.inbound_message_repository import (
    StoredInboundMessage,
)
from loka.bounded_contexts.messaging.domain.value_objects.inbound_message import (
    MessageKind,
    SharedLocation,
)
from loka.shared.application.unit_of_work import UnitOfWork

pytestmark = pytest.mark.unit

NOW = datetime(2026, 3, 1, 9, 0, tzinfo=UTC)
PHONE = "+237699123456"

BASTOS = SharedLocation(latitude=3.8905, longitude=11.5139, label="Bastos, Yaoundé")
OPEN_SEA = SharedLocation(latitude=-30.0, longitude=-20.0)


@dataclass
class FakeInbound:
    rows: dict[uuid.UUID, StoredInboundMessage] = field(default_factory=dict)

    async def get_by_id(self, message_id: uuid.UUID) -> StoredInboundMessage | None:
        return self.rows.get(message_id)

    async def get_by_external_id(self, **_: Any) -> StoredInboundMessage | None:
        return None

    async def add_if_absent(self, message: Any) -> StoredInboundMessage | None:
        return None

    async def attach_conversation(self, *_: Any, **__: Any) -> None:
        return None


@dataclass
class FakeSessions:
    store: dict[uuid.UUID, ConversationSession] = field(default_factory=dict)

    async def get_by_id(self, session_id: uuid.UUID) -> ConversationSession | None:
        return self.store.get(session_id)

    async def latest_by_phone(self, phone: PhoneNumber) -> ConversationSession | None:
        return next((s for s in self.store.values() if s.phone.e164 == phone.e164), None)

    async def add(self, session: ConversationSession) -> None:
        self.store[session.id] = session

    async def save(self, session: ConversationSession, **_: Any) -> None:
        self.store[session.id] = session


@dataclass
class FakeTurns:
    turns: list[ConversationTurn] = field(default_factory=list)

    async def add(self, turn: ConversationTurn) -> uuid.UUID:
        self.turns.append(turn)
        return uuid.uuid4()

    async def recent(self, session_id: uuid.UUID, *, limit: int = 5) -> list[TurnSummary]:
        return []


@dataclass
class FakeLedger:
    entries: list[UsageEntry] = field(default_factory=list)

    async def append(self, entry: UsageEntry) -> uuid.UUID:
        self.entries.append(entry)
        return uuid.uuid4()

    async def total_cost_usd(self, **_: Any) -> Any:
        return 0


class RefusingModel:
    """Any call is a failure: these turns must never reach a model."""

    def __init__(self) -> None:
        self.calls = 0

    async def understand_intent(self, prompt: Any) -> Any:
        self.calls += 1
        raise AssertionError("a pin or a photo must not be sent to a model")


class FakeListing:
    def __init__(self) -> None:
        self.attached: list[tuple[uuid.UUID, str, str]] = []

    async def register_from_conversation(self, **_: Any) -> Any:
        return None

    async def attach_media_from_conversation(
        self,
        *,
        property_id: uuid.UUID,
        object_key: str,
        kind: str,
        now: datetime,
    ) -> bool:
        self.attached.append((property_id, object_key, kind))
        return True


class FakeUow(UnitOfWork):
    def __init__(self) -> None:
        self.commits = 0
        self._events = _Publisher()

    async def __aenter__(self) -> FakeUow:
        return self

    async def __aexit__(self, *args: object) -> None:
        return None

    def __enter__(self) -> FakeUow:
        return self

    def __exit__(self, *args: object) -> None:
        return None

    async def commit(self) -> None:
        self.commits += 1

    async def rollback(self) -> None:
        return None

    @property
    def events(self) -> Any:
        return self._events

    def collect(self, aggregate: object) -> None:
        return None

    def repository(self, name: str) -> Any:
        raise NotImplementedError


@dataclass
class _Publisher:
    staged: list[Any] = field(default_factory=list)

    def stage(self, events: Any, *, correlation_id: str = "test") -> None:
        self.staged.extend(events)


def _session(*, flow: FlowName, step: FlowStep, context: dict[str, Any] | None = None,
             property_id: uuid.UUID | None = None) -> ConversationSession:
    session = ConversationSession(
        session_id=uuid.uuid4(),
        user_id=uuid.uuid4(),
        phone=PhoneNumber.normalize(PHONE),
        now=NOW,
        flow=flow,
    )
    session.step = step
    if context:
        session.context.update(context)
    if property_id is not None:
        session.assigned_property_id = property_id
    return session


def _build(
    session: ConversationSession,
    *,
    kind: MessageKind,
    location: SharedLocation | None = None,
    media_object_key: str | None = None,
    text: str | None = None,
    listing: FakeListing | None = None,
) -> tuple[AnalyseConversationMessageUseCase, uuid.UUID, RefusingModel, FakeUow]:
    message_id = uuid.uuid4()
    inbound = FakeInbound(
        rows={
            message_id: StoredInboundMessage(
                message_id=message_id,
                session_id=session.id,
                sender_user_id=session.user_id,
                processed=False,
                text=text,
                kind=kind,
                location=location,
                media_object_key=media_object_key,
            )
        }
    )
    model = RefusingModel()
    uow = FakeUow()
    use_case = AnalyseConversationMessageUseCase(
        uow,
        inbound=inbound,
        sessions=FakeSessions(store={session.id: session}),
        turns=FakeTurns(),
        usage=FakeLedger(),
        model=model,
        listing=listing,
    )
    return use_case, message_id, model, uow


class TestASharedPositionAnswersTheLocationQuestion:
    async def test_a_pin_is_read_as_a_location_without_a_model(self) -> None:
        session = _session(flow=FlowName.PROPERTY_CREATION, step=FlowStep.COLLECT_LOCATION)
        use_case, message_id, model, _ = _build(
            session, kind=MessageKind.LOCATION, location=BASTOS
        )

        report = await use_case.execute(
            AnalysisCommand(message_id=message_id, session_id=session.id), now=NOW
        )

        assert model.calls == 0
        assert report.used_llm is False
        assert report.accepted is True
        assert report.facts["location"]["city"] == "Yaoundé"

    async def test_the_city_question_is_not_asked_after_a_pin(self) -> None:
        """E3's exact wording: a validated location, with no question of city."""
        session = _session(flow=FlowName.PROPERTY_CREATION, step=FlowStep.COLLECT_LOCATION)
        use_case, message_id, _, _ = _build(
            session, kind=MessageKind.LOCATION, location=BASTOS
        )

        report = await use_case.execute(
            AnalysisCommand(message_id=message_id, session_id=session.id), now=NOW
        )

        assert report.next_step != FlowStep.COLLECT_LOCATION.value
        assert report.question_key != "ask.location"
        assert session.step is not FlowStep.COLLECT_LOCATION

    async def test_the_place_name_becomes_the_neighbourhood(self) -> None:
        session = _session(flow=FlowName.PROPERTY_CREATION, step=FlowStep.COLLECT_LOCATION)
        use_case, message_id, _, _ = _build(
            session, kind=MessageKind.LOCATION, location=BASTOS
        )

        report = await use_case.execute(
            AnalysisCommand(message_id=message_id, session_id=session.id), now=NOW
        )

        assert report.facts["location"]["neighbourhood"] == "Bastos"

    async def test_the_coordinates_travel_with_the_location(self) -> None:
        """The pin is what makes the listing precise enough to publish."""
        session = _session(flow=FlowName.PROPERTY_CREATION, step=FlowStep.COLLECT_LOCATION)
        use_case, message_id, _, _ = _build(
            session, kind=MessageKind.LOCATION, location=BASTOS
        )

        report = await use_case.execute(
            AnalysisCommand(message_id=message_id, session_id=session.id), now=NOW
        )

        location = report.facts["location"]
        assert location["latitude"] == pytest.approx(3.8905)
        assert location["longitude"] == pytest.approx(11.5139)

    async def test_a_tenant_searching_is_located_by_a_pin_too(self) -> None:
        session = _session(
            flow=FlowName.PROPERTY_SEARCH, step=FlowStep.COLLECT_SEARCH_CRITERIA
        )
        use_case, message_id, model, _ = _build(
            session, kind=MessageKind.LOCATION, location=BASTOS
        )

        report = await use_case.execute(
            AnalysisCommand(message_id=message_id, session_id=session.id), now=NOW
        )

        assert model.calls == 0
        assert report.facts["location"]["city"] == "Yaoundé"

    async def test_a_pin_nobody_can_place_leaves_the_question_open(self) -> None:
        """Naming a city we cannot justify would file the listing in the wrong town."""
        session = _session(
            flow=FlowName.PROPERTY_CREATION,
            step=FlowStep.COLLECT_LOCATION,
            # The rest of the form is answered, so the location is the only
            # thing left to ask for and the question cannot be about anything
            # else.
            context={"property_type": "APARTMENT", "standing": "MODERN", "quantity": 1},
        )
        use_case, message_id, _, _ = _build(
            session, kind=MessageKind.LOCATION, location=OPEN_SEA
        )

        report = await use_case.execute(
            AnalysisCommand(message_id=message_id, session_id=session.id), now=NOW
        )

        assert "location" not in report.facts
        assert report.rejected_facts.get("location") is not None
        assert report.question_key == "ask.location"

    async def test_a_pin_sent_with_a_caption_still_reads_as_a_position(self) -> None:
        session = _session(flow=FlowName.PROPERTY_CREATION, step=FlowStep.COLLECT_LOCATION)
        use_case, message_id, model, _ = _build(
            session, kind=MessageKind.LOCATION, location=BASTOS, text="c'est ici"
        )

        report = await use_case.execute(
            AnalysisCommand(message_id=message_id, session_id=session.id), now=NOW
        )

        assert model.calls == 0
        assert report.facts["location"]["city"] == "Yaoundé"


class TestAnImageIsAttachedToTheListing:
    async def test_a_photo_at_the_media_step_reaches_the_listing(self) -> None:
        property_id = uuid.uuid4()
        session = _session(
            flow=FlowName.PROPERTY_CREATION,
            step=FlowStep.COLLECT_MEDIA,
            property_id=property_id,
        )
        listing = FakeListing()
        use_case, message_id, model, _ = _build(
            session,
            kind=MessageKind.IMAGE,
            media_object_key="chat/abc/photo.jpg",
            listing=listing,
        )

        report = await use_case.execute(
            AnalysisCommand(message_id=message_id, session_id=session.id), now=NOW
        )

        assert model.calls == 0
        assert listing.attached == [(property_id, "chat/abc/photo.jpg", "PHOTO")]
        assert report.accepted is True

    async def test_a_photo_with_no_listing_yet_is_not_lost_silently(self) -> None:
        """Nothing to attach it to: the turn is still recorded and reported."""
        session = _session(flow=FlowName.PROPERTY_CREATION, step=FlowStep.COLLECT_MEDIA)
        listing = FakeListing()
        use_case, message_id, _, _ = _build(
            session,
            kind=MessageKind.IMAGE,
            media_object_key="chat/abc/photo.jpg",
            listing=listing,
        )

        report = await use_case.execute(
            AnalysisCommand(message_id=message_id, session_id=session.id), now=NOW
        )

        assert listing.attached == []
        assert report.reason == "media_without_a_listing"

    async def test_a_document_is_attached_as_a_document_not_a_photo(self) -> None:
        property_id = uuid.uuid4()
        session = _session(
            flow=FlowName.PROPERTY_CREATION,
            step=FlowStep.COLLECT_MEDIA,
            property_id=property_id,
        )
        listing = FakeListing()
        use_case, message_id, _, _ = _build(
            session,
            kind=MessageKind.DOCUMENT,
            media_object_key="chat/abc/plan.pdf",
            listing=listing,
        )

        await use_case.execute(
            AnalysisCommand(message_id=message_id, session_id=session.id), now=NOW
        )

        assert listing.attached == [(property_id, "chat/abc/plan.pdf", "DOCUMENT")]
