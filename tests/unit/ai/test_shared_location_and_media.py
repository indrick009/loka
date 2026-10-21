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
from datetime import UTC, datetime, timedelta
from typing import Any

import pytest

from loka.bounded_contexts.ai.application.ports import (
    ConversationTurn,
    TurnSummary,
    UsageEntry,
)
from loka.bounded_contexts.ai.application.use_cases.analyse_conversation_message import (
    PENDING_MEDIA_KEY,
    PHOTO_BURST_SECONDS,
    AnalyseConversationMessageUseCase,
    AnalysisCommand,
    _is_a_question,
    _keeping_the_place,
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
from loka.bounded_contexts.property.application.use_cases.create_property_from_conversation import (
    ConversationMediaResult,
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
    ) -> ConversationMediaResult:
        self.attached.append((property_id, object_key, kind))
        return ConversationMediaResult(
            photos=len(self.attached), published=True, just_published=len(self.attached) == 1
        )


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
    media_refused: str | None = None,
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
                media_refused=media_refused,
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

    async def test_a_photo_sent_before_its_listing_is_kept_for_it(self) -> None:
        """Landlords send the photos first. They used to be dropped, and the
        landlord asked to rephrase a message that was a picture."""
        session = _session(flow=FlowName.PROPERTY_CREATION, step=FlowStep.COLLECT_LOCATION)
        listing = FakeListing()
        use_case, message_id, _, uow = _build(
            session,
            kind=MessageKind.IMAGE,
            media_object_key="chat/abc/photo.jpg",
            listing=listing,
        )

        report = await use_case.execute(
            AnalysisCommand(message_id=message_id, session_id=session.id), now=NOW
        )

        assert listing.attached == []
        assert report.reason == "media_kept_for_listing"
        assert session.context[PENDING_MEDIA_KEY] == [
            {"key": "chat/abc/photo.jpg", "kind": "PHOTO"}
        ]
        assert "garde" in uow.events.staged[0].text

    async def test_a_photo_is_answered_once_per_album(self) -> None:
        """Ten photos used to get ten "photo bien reçue": the reply names its
        photo and waits, so only the last of a burst is sent."""
        property_id = uuid.uuid4()
        session = _session(
            flow=FlowName.PROPERTY_CREATION, step=FlowStep.COLLECT_MEDIA, property_id=property_id
        )
        use_case, message_id, _, uow = _build(
            session,
            kind=MessageKind.IMAGE,
            media_object_key="chat/abc/photo.jpg",
            listing=FakeListing(),
        )

        await use_case.execute(
            AnalysisCommand(message_id=message_id, session_id=session.id), now=NOW
        )

        staged = uow.events.staged[0]
        assert staged.media_burst_of == message_id
        assert staged.not_before == NOW + timedelta(seconds=PHOTO_BURST_SECONDS)

    async def test_a_video_is_attached_as_a_video_not_a_photo(self) -> None:
        property_id = uuid.uuid4()
        session = _session(
            flow=FlowName.PROPERTY_CREATION, step=FlowStep.COLLECT_MEDIA, property_id=property_id
        )
        listing = FakeListing()
        use_case, message_id, _, _ = _build(
            session, kind=MessageKind.VIDEO, media_object_key="chat/abc/tour.mp4", listing=listing
        )

        await use_case.execute(
            AnalysisCommand(message_id=message_id, session_id=session.id), now=NOW
        )

        assert listing.attached == [(property_id, "chat/abc/tour.mp4", "VIDEO")]

    @pytest.mark.parametrize("kind", [MessageKind.AUDIO, MessageKind.STICKER])
    async def test_a_voice_note_or_a_sticker_is_never_a_photo(self, kind: MessageKind) -> None:
        """Both used to be filed as photos of the lodging."""
        property_id = uuid.uuid4()
        session = _session(
            flow=FlowName.PROPERTY_CREATION, step=FlowStep.COLLECT_MEDIA, property_id=property_id
        )
        listing = FakeListing()
        use_case, message_id, _, uow = _build(
            session, kind=kind, media_object_key="chat/abc/note.ogg", listing=listing
        )

        report = await use_case.execute(
            AnalysisCommand(message_id=message_id, session_id=session.id), now=NOW
        )

        assert listing.attached == []
        assert report.reason == "media_unsupported"
        assert uow.events.staged[0].text

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


class TestAPinAndADistrictAreOnePlace:
    """Each used to replace the other: typing "Odza" after the pin lost the
    pin, sharing the pin after "Odza" lost the name."""

    def test_a_district_typed_after_the_pin_keeps_the_pin(self) -> None:
        session = _session(
            flow=FlowName.PROPERTY_CREATION,
            step=FlowStep.COLLECT_LOCATION,
            context={"location": {"city": "Yaoundé", "latitude": 3.79, "longitude": 11.537}},
        )

        merged = _keeping_the_place({"neighbourhood": "Odza"}, session)

        assert merged == {
            "neighbourhood": "Odza",
            "city": "Yaoundé",
            "latitude": 3.79,
            "longitude": 11.537,
        }

    def test_a_pin_shared_after_the_district_keeps_its_name(self) -> None:
        session = _session(
            flow=FlowName.PROPERTY_CREATION,
            step=FlowStep.COLLECT_LOCATION,
            context={"location": {"city": "Yaoundé", "neighbourhood": "Odza"}},
        )

        merged = _keeping_the_place({"latitude": 3.79, "longitude": 11.537}, session)

        assert merged["neighbourhood"] == "Odza"

    def test_a_place_in_another_city_starts_over(self) -> None:
        session = _session(
            flow=FlowName.PROPERTY_CREATION,
            step=FlowStep.COLLECT_LOCATION,
            context={"location": {"city": "Yaoundé", "neighbourhood": "Odza"}},
        )

        merged = _keeping_the_place({"latitude": 4.05, "longitude": 9.70}, session)

        assert "neighbourhood" not in merged


class TestAVideoTooHeavyToStore:
    """The gateway used to drop it: no bytes, no text, no answer at all."""

    async def test_he_is_told_why_and_what_to_send_instead(self) -> None:
        session = _session(
            flow=FlowName.PROPERTY_CREATION,
            step=FlowStep.COLLECT_MEDIA,
            property_id=uuid.uuid4(),
        )
        listing = FakeListing()
        use_case, message_id, model, uow = _build(
            session, kind=MessageKind.VIDEO, listing=listing, media_refused="too_large"
        )

        report = await use_case.execute(
            AnalysisCommand(message_id=message_id, session_id=session.id), now=NOW
        )

        assert model.calls == 0
        assert listing.attached == []
        assert report.reason == "media_refused"
        staged = uow.events.staged[0]
        assert "16 Mo" in staged.text
        assert staged.answering == "(vidéo envoyée)"


class TestWhatTheLiveTestShowed:
    """Three defects from the landlord's live test of 21 October."""

    @pytest.mark.parametrize(
        "text",
        ["Tu as retenu la localisation ?", "est-ce que la caution est notée", "pourquoi"],
    )
    def test_a_question_is_never_a_confirmation(self, text: str) -> None:
        assert _is_a_question(text)

    @pytest.mark.parametrize("text", ["oui", "Oui c'est bon publie", "ok vas-y"])
    def test_a_yes_is_still_a_yes(self, text: str) -> None:
        assert not _is_a_question(text)

    async def test_the_photo_that_publishes_moves_the_thread_to_published(self) -> None:
        """Left on COLLECT_MEDIA, Kora kept asking for the photos it had."""
        session = _session(
            flow=FlowName.PROPERTY_CREATION,
            step=FlowStep.COLLECT_MEDIA,
            property_id=uuid.uuid4(),
        )
        use_case, message_id, _, _ = _build(
            session,
            kind=MessageKind.IMAGE,
            media_object_key="chat/abc/photo.jpg",
            listing=FakeListing(),
        )

        await use_case.execute(
            AnalysisCommand(message_id=message_id, session_id=session.id), now=NOW
        )

        assert session.step is FlowStep.PUBLISHED

    async def test_a_fast_lane_answer_is_said_in_his_voice(self) -> None:
        """"2", "6", "60mil" were answered by stock copy in "vous"."""
        session = _session(
            flow=FlowName.PROPERTY_CREATION,
            step=FlowStep.COLLECT_DEPOSIT,
            context={
                "property_type": "STUDIO",
                "standing": "MODERN",
                "quantity": 1,
                "location": {"city": "Yaoundé", "neighbourhood": "Odza"},
                "rent": 60_000,
                "charges": 0,
                "charging_policy": "EXTRA",
                "next_question": "ask.deposit",
            },
        )
        use_case, message_id, model, uow = _build(session, kind=MessageKind.TEXT, text="2")

        await use_case.execute(
            AnalysisCommand(message_id=message_id, session_id=session.id), now=NOW
        )

        assert model.calls == 0
        assert uow.events.staged[0].answering == "2"
