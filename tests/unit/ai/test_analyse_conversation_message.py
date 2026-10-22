"""The analysis pipeline: what it costs, what it trusts, what it refuses.

Every guarantee the pipeline claims is asserted here against fakes, because the
point of these tests is the *decisions*: whether a message cost a model call,
whether a transaction was open while it did, and whether an untrusted value ever
reached the session.
"""

from __future__ import annotations

import json
import uuid
from collections.abc import Iterable
from dataclasses import dataclass, field
from datetime import UTC, datetime
from decimal import Decimal
from typing import Any

import pytest

from loka.bounded_contexts.ai.application.ports import (
    AvailabilityAnswerResult,
    BudgetPolicy,
    ConversationPropertyHit,
    ConversationSearchQuery,
    ConversationTurn,
    IntentModel,
    TurnSummary,
    UsageEntry,
)
from loka.bounded_contexts.ai.application.use_cases.analyse_conversation_message import (
    USE_CASE,
    AnalyseConversationMessageUseCase,
    AnalysisCommand,
)
from loka.bounded_contexts.ai.domain.services.ai_provider import (
    IntentResult,
    LlmResponse,
    LlmUsage,
    Prompt,
)
from loka.bounded_contexts.identity.domain.value_objects.phone_number import PhoneNumber
from loka.bounded_contexts.messaging.domain.entities.conversation_session import (
    ConversationSession,
    FlowName,
    FlowStep,
)
from loka.bounded_contexts.messaging.domain.events.message_events import (
    WhatsAppMessageSendRequested,
)
from loka.bounded_contexts.messaging.domain.repositories.inbound_message_repository import (
    StoredInboundMessage,
)
from loka.shared.application.unit_of_work import UnitOfWork
from loka.shared.domain.errors import (
    ConcurrencyConflict,
    ExternalServiceUnavailable,
    InvalidStateTransition,
)

pytestmark = pytest.mark.unit

NOW = datetime(2026, 3, 1, 9, 0, tzinfo=UTC)
PHONE = "+237699123456"
TEST_MODEL = "google/gemini-2.5-flash"
# Deliberately unresolvable by the regex rules: these tests are about what
# happens once the classifier has abstained. A text the rules *do* resolve would
# make them pass for the wrong reason.
AMBIGUOUS_TEXT = "bonjour je voulais demander quelque chose"


@dataclass
class FakeInboundRepository:
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
class FakeSessionRepository:
    store: dict[uuid.UUID, ConversationSession] = field(default_factory=dict)
    saved: list[tuple[uuid.UUID, int | None]] = field(default_factory=list)
    conflict_on_save: bool = False

    async def get_by_id(self, session_id: uuid.UUID) -> ConversationSession | None:
        return self.store.get(session_id)

    async def latest_by_phone(self, phone: PhoneNumber) -> ConversationSession | None:
        return next((s for s in self.store.values() if s.phone.e164 == phone.e164), None)

    async def add(self, session: ConversationSession) -> None:
        self.store[session.id] = session

    async def save(
        self, session: ConversationSession, *, expected_revision: int | None = None
    ) -> None:
        self.saved.append((session.id, expected_revision))
        if self.conflict_on_save:
            raise ConcurrencyConflict(
                "conversation session was modified concurrently",
                context={"session_id": str(session.id)},
            )
        self.store[session.id] = session


@dataclass
class FakeTurnRepository:
    turns: list[ConversationTurn] = field(default_factory=list)
    history: list[TurnSummary] = field(default_factory=list)

    async def add(self, turn: ConversationTurn) -> uuid.UUID:
        self.turns.append(turn)
        return uuid.uuid4()

    async def recent(self, session_id: uuid.UUID, *, limit: int = 5) -> list[TurnSummary]:
        return self.history[-limit:]


@dataclass
class FakeLedger:
    entries: list[UsageEntry] = field(default_factory=list)
    day_spend: Decimal = Decimal("0")
    user_spend: Decimal = Decimal("0")
    read_user_filter: bool = True

    async def append(self, entry: UsageEntry) -> uuid.UUID:
        self.entries.append(entry)
        return uuid.uuid4()

    async def total_cost_usd(
        self, *, on: Any, user_id: uuid.UUID | None = None
    ) -> Decimal:
        if user_id is not None and self.read_user_filter:
            return self.user_spend
        return self.day_spend


@dataclass
class FakeEventPublisher:
    """Records what the turn owes the user instead of publishing it."""

    staged: list[Any] = field(default_factory=list)

    def stage(self, events: Iterable[Any], *, correlation_id: str) -> None:
        self.staged.extend(events)

    async def flush(self) -> None:
        return None


@dataclass
class StubModel:
    """Records whether it was called, and how often."""

    result: IntentResult
    calls: int = 0
    prompts: list[Prompt] = field(default_factory=list)

    @property
    def model(self) -> str:
        return TEST_MODEL

    async def understand_intent(self, prompt: Prompt) -> IntentResult:
        self.calls += 1
        self.prompts.append(prompt)
        return self.result


@dataclass
class StubResponseModel:
    """A reply writer: returns a canned sentence, or raises on demand."""

    text: str = "Très bien, quel est le loyer mensuel ?"
    cost: float = 0.0001
    error: Exception | None = None
    calls: int = 0
    prompts: list[Prompt] = field(default_factory=list)

    @property
    def model(self) -> str:
        return TEST_MODEL

    async def generate_response(self, prompt: Prompt) -> LlmResponse:
        self.calls += 1
        self.prompts.append(prompt)
        if self.error is not None:
            raise self.error
        return LlmResponse(
            content=self.text,
            usage=LlmUsage(
                input_tokens=50,
                output_tokens=20,
                latency_ms=120,
                estimated_cost_usd=self.cost,
                model=TEST_MODEL,
            ),
        )


class FakeUnitOfWork(UnitOfWork):
    def __init__(self) -> None:
        self.commits = 0
        self.open_commits: list[int] = []
        self.commits_at_model_call: list[int] = []
        self._events = FakeEventPublisher()

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
        return None

    @property
    def events(self) -> FakeEventPublisher:
        return self._events

    def collect(self, aggregate: object) -> None:
        return None

    def repository(self, name: str) -> Any:
        raise NotImplementedError


def _model_result(
    *,
    intent: str = "CREATE_PROPERTY",
    confidence: float = 0.9,
    entities: dict[str, Any] | None = None,
    cost: float = 0.0002,
    reply: str | None = None,
    asking: str | None = None,
    language: str = "fr",
) -> IntentResult:
    return IntentResult(
        intent=intent,
        confidence=confidence,
        entities=entities if entities is not None else {"property_type": "APARTMENT"},
        language=language,
        reply=reply,
        asking=asking,
        usage=LlmUsage(
            input_tokens=100,
            output_tokens=40,
            latency_ms=250,
            estimated_cost_usd=cost,
            model=TEST_MODEL,
        ),
    )


@pytest.fixture
def session() -> ConversationSession:
    return ConversationSession(
        session_id=uuid.uuid4(),
        user_id=uuid.uuid4(),
        phone=PhoneNumber.normalize(PHONE),
        now=NOW,
    )


@pytest.fixture
def message_id() -> uuid.UUID:
    return uuid.uuid4()


@pytest.fixture
def uow() -> FakeUnitOfWork:
    return FakeUnitOfWork()


def build(
    uow: FakeUnitOfWork,
    session: ConversationSession,
    message_id: uuid.UUID,
    *,
    text: str = AMBIGUOUS_TEXT,
    model: IntentModel | None = None,
    response_model: Any | None = None,
    budget: BudgetPolicy | None = None,
    ledger: FakeLedger | None = None,
    sessions: FakeSessionRepository | None = None,
    search: Any | None = None,
    availability: Any | None = None,
    availability_requests: Any | None = None,
    demands: Any | None = None,
    follow_up: Any | None = None,
) -> tuple[
    AnalyseConversationMessageUseCase,
    FakeTurnRepository,
    FakeLedger,
    FakeSessionRepository,
]:
    turns = FakeTurnRepository()
    usage = ledger or FakeLedger()
    session_repo = sessions or FakeSessionRepository(store={session.id: session})
    inbound = FakeInboundRepository(
        rows={
            message_id: StoredInboundMessage(
                message_id=message_id,
                session_id=session.id,
                sender_user_id=session.user_id,
                processed=False,
                text=text,
            )
        }
    )
    use_case = AnalyseConversationMessageUseCase(
        uow,
        inbound=inbound,
        sessions=session_repo,
        turns=turns,
        usage=usage,
        model=model,
        response_model=response_model,
        budget=budget,
        search=search,
        availability=availability,
        availability_requests=availability_requests,
        demands=demands,
        follow_up=follow_up,
    )
    return use_case, turns, usage, session_repo


class TestDeterministicShortCircuit:
    async def test_a_rule_hit_never_calls_the_model(
        self,
        uow: FakeUnitOfWork,
        session: ConversationSession,
        message_id: uuid.UUID,
    ) -> None:
        model = StubModel(result=_model_result())
        use_case, turns, usage, _ = build(
            uow, session, message_id, text="250 000 fcfa", model=model
        )

        report = await use_case.execute(
            AnalysisCommand(message_id=message_id, session_id=session.id), now=NOW
        )

        assert model.calls == 0
        assert report.used_llm is False
        assert usage.entries == []
        assert turns.turns[0].used_llm is False

    async def test_a_rule_hit_still_records_the_turn(
        self,
        uow: FakeUnitOfWork,
        session: ConversationSession,
        message_id: uuid.UUID,
    ) -> None:
        use_case, turns, _, _ = build(uow, session, message_id, text="250 000 fcfa")

        await use_case.execute(
            AnalysisCommand(message_id=message_id, session_id=session.id), now=NOW
        )

        assert len(turns.turns) == 1
        assert turns.turns[0].intent == "COLLECT_PRICE"
        assert turns.turns[0].used_llm is False

    async def test_a_rule_hit_merges_the_accepted_facts(
        self,
        uow: FakeUnitOfWork,
        session: ConversationSession,
        message_id: uuid.UUID,
    ) -> None:
        use_case, _, _, sessions = build(uow, session, message_id, text="250 000 fcfa")

        await use_case.execute(
            AnalysisCommand(message_id=message_id, session_id=session.id), now=NOW
        )

        assert session.context["rent"] == 250_000
        assert sessions.saved

    async def test_a_bare_non_answers_the_standing_question(
        self,
        uow: FakeUnitOfWork,
        session: ConversationSession,
        message_id: uuid.UUID,
    ) -> None:
        """Once the platform asked "moderne ou non", a bare "non" is a value for
        the field, not a refusal: the fast lane must read the open question."""
        session.start_flow(
            FlowName.PROPERTY_CREATION, now=NOW, first_step=FlowStep.COLLECT_STANDING
        )
        session.context["property_type"] = "STUDIO"
        session.context["next_question"] = "ask.standing"
        model = StubModel(result=_model_result())

        use_case, _, _, _ = build(uow, session, message_id, text="non", model=model)

        report = await use_case.execute(
            AnalysisCommand(message_id=message_id, session_id=session.id), now=NOW
        )

        assert model.calls == 0
        assert session.context["standing"] == "NON_MODERN"
        # Step 3 of the form, which the standing answer unblocks.
        assert report.next_step == "COLLECT_QUANTITY"

    async def test_a_bare_oui_answers_the_charges_question(
        self,
        uow: FakeUnitOfWork,
        session: ConversationSession,
        message_id: uuid.UUID,
    ) -> None:
        """Observed live: a bare "oui" to "l'eau et l'électricité sont-elles
        incluses ?" was read as a dialogue act that carried no value, produced no
        step, and the sentence the writer sent named the caution instead — so the
        landlord's next "2" was filed as the charges. The fast lane reads it here
        as the value of the open question, off the model entirely."""
        session.start_flow(
            FlowName.PROPERTY_CREATION, now=NOW, first_step=FlowStep.COLLECT_CHARGES
        )
        session.context.update(
            {
                "property_type": "STUDIO",
                "standing": "MODERN",
                "quantity": 1,
                "location": {"city": "Yaoundé", "neighbourhood": "Bastos"},
                "rent": 70_000,
                "next_question": "ask.charges",
            }
        )
        model = StubModel(result=_model_result())

        use_case, turns, _, _ = build(uow, session, message_id, text="Oui", model=model)

        report = await use_case.execute(
            AnalysisCommand(message_id=message_id, session_id=session.id), now=NOW
        )

        assert model.calls == 0
        assert turns.turns[0].used_llm is False
        assert session.context["charging_policy"] == "INCLUDED"
        # The deposit is the next open field once the charges are answered.
        assert report.next_step == "COLLECT_DEPOSIT"
        assert report.question_key == "ask.deposit"

    async def test_a_bare_greeting_costs_no_model_call(
        self,
        uow: FakeUnitOfWork,
        session: ConversationSession,
        message_id: uuid.UUID,
    ) -> None:
        """The most frequent message carries no intent: it is answered from a
        template, and the turn is still recorded."""
        model = StubModel(result=_model_result())

        use_case, turns, usage, _ = build(
            uow, session, message_id, text="Bonjour", model=model
        )

        report = await use_case.execute(
            AnalysisCommand(message_id=message_id, session_id=session.id), now=NOW
        )

        assert model.calls == 0
        assert report.intent == "SUPPORT"
        assert usage.entries == []
        assert turns.turns[0].used_llm is False


class TestModelPath:
    async def test_an_ambiguous_message_falls_back_to_the_model(
        self,
        uow: FakeUnitOfWork,
        session: ConversationSession,
        message_id: uuid.UUID,
    ) -> None:
        model = StubModel(result=_model_result())
        use_case, turns, _, _ = build(
            uow,
            session,
            message_id,
            text=AMBIGUOUS_TEXT,
            model=model,
        )

        report = await use_case.execute(
            AnalysisCommand(message_id=message_id, session_id=session.id), now=NOW
        )

        assert model.calls == 1
        assert report.used_llm is True
        assert turns.turns[0].used_llm is True

    async def test_the_call_is_costed_and_attributed(
        self,
        uow: FakeUnitOfWork,
        session: ConversationSession,
        message_id: uuid.UUID,
    ) -> None:
        use_case, _, usage, _ = build(
            uow, session, message_id, model=StubModel(result=_model_result(cost=0.0004))
        )

        report = await use_case.execute(
            AnalysisCommand(message_id=message_id, session_id=session.id), now=NOW
        )

        assert len(usage.entries) == 1
        entry = usage.entries[0]
        assert entry.use_case == USE_CASE
        assert entry.cost_usd == Decimal("0.0004")
        assert entry.user_id == session.user_id
        assert entry.conversation_id == session.id
        assert entry.input_tokens == 100
        assert report.cost_usd == Decimal("0.0004")

    async def test_no_transaction_is_open_while_the_model_runs(
        self,
        uow: FakeUnitOfWork,
        session: ConversationSession,
        message_id: uuid.UUID,
    ) -> None:
        """The connection is the scarce resource: holding one across a network
        call is how a slow provider takes the whole worker pool down."""

        class ObservantModel:
            @property
            def model(self) -> str:
                return TEST_MODEL

            async def understand_intent(self, prompt: Prompt) -> IntentResult:
                uow.commits_at_model_call.append(uow.commits)
                return _model_result()

        use_case, _, _, _ = build(
            uow, session, message_id, text="une phrase indeterminate", model=ObservantModel()
        )

        await use_case.execute(
            AnalysisCommand(message_id=message_id, session_id=session.id), now=NOW
        )

        assert uow.commits_at_model_call == [1]

    async def test_the_turn_and_the_facts_land_in_one_commit(
        self,
        uow: FakeUnitOfWork,
        session: ConversationSession,
        message_id: uuid.UUID,
    ) -> None:
        before = session.revision
        use_case, _, _, sessions = build(
            uow,
            session,
            message_id,
            text="une phrase indeterminate",
            model=StubModel(result=_model_result()),
        )

        await use_case.execute(
            AnalysisCommand(message_id=message_id, session_id=session.id), now=NOW
        )

        # One commit releases the transaction, one commit persists the turn, the
        # usage and the session together. The save must carry the revision read
        # at load time, not the one this analysis produced.
        assert uow.commits == 2
        assert sessions.saved == [(session.id, before)]


class TestLlmFirst:
    async def test_a_rule_readable_message_still_reaches_the_model(
        self,
        uow: FakeUnitOfWork,
        session: ConversationSession,
        message_id: uuid.UUID,
    ) -> None:
        """The rules are a fast lane, not the primary reader.

        A full sentence the regexes could classify must still go to the model,
        because only the model reads it together with the conversation state.
        """

        model = StubModel(result=_model_result(intent="PROPERTY_SEARCH"))
        use_case, _, _, _ = build(
            uow,
            session,
            message_id,
            text="je cherche un appartement à Bastos",
            model=model,
        )

        report = await use_case.execute(
            AnalysisCommand(message_id=message_id, session_id=session.id), now=NOW
        )

        assert model.calls == 1
        assert report.used_llm is True

    async def test_the_prompt_carries_the_conversation_state(
        self,
        uow: FakeUnitOfWork,
        session: ConversationSession,
        message_id: uuid.UUID,
    ) -> None:
        """A bare answer is only intelligible with the question it answers."""

        session.start_flow(
            FlowName.PROPERTY_CREATION, now=NOW, first_step=FlowStep.COLLECT_LOCATION
        )
        session.context["next_question"] = "ask.location"
        session.context["property_type"] = "APARTMENT"

        model = StubModel(result=_model_result(intent="COLLECT_LOCATION"))
        use_case, _, _, _ = build(uow, session, message_id, text="Bastos", model=model)

        await use_case.execute(
            AnalysisCommand(message_id=message_id, session_id=session.id), now=NOW
        )

        payload = json.loads(model.prompts[0].user)
        conversation = payload["conversation"]
        assert conversation["flow"] == "PROPERTY_CREATION"
        assert conversation["step"] == "COLLECT_LOCATION"
        assert conversation["awaiting_answer_to"] == "ask.location"
        # The model is told not only *which* question is open but *which entity*
        # a short answer fills, so a bare value cannot drift to another field.
        assert conversation["awaiting_field"] == "city and neighbourhood"
        assert conversation["known_facts"]["property_type"] == "APARTMENT"

    async def test_an_opening_search_shows_the_tenant_form_to_the_model(
        self,
        uow: FakeUnitOfWork,
        session: ConversationSession,
        message_id: uuid.UUID,
    ) -> None:
        """The session is still on SUPPORT when the tenant opens a search, so a
        prompt built from the session would hand the model the landlord's fields
        — rent, charges, deposit — that a searching tenant cannot answer."""
        model = StubModel(
            result=_model_result(
                intent="PROPERTY_SEARCH",
                entities={"property_type": "STUDIO", "standing": "MODERN", "quantity": 1},
            )
        )
        use_case, _, _, _ = build(
            uow,
            session,
            message_id,
            text="je veux louer un studio moderne",
            model=model,
        )

        await use_case.execute(
            AnalysisCommand(message_id=message_id, session_id=session.id), now=NOW
        )

        payload = json.loads(model.prompts[0].user)
        assert payload["conversation"]["flow"] == "PROPERTY_SEARCH"
        assert "tenant" in payload["you_are_helping"]
        assert [field["field"] for field in payload["required_fields"]] == [
            "property_type",
            "location",
            "rent",
            "advance_months",
        ]


class TestPendingAnswerBinding:
    async def test_a_bare_number_fills_the_open_question(
        self,
        uow: FakeUnitOfWork,
        session: ConversationSession,
        message_id: uuid.UUID,
    ) -> None:
        """A lone "5" answered to "durée minimale" is five months and nothing
        else: the open question binds it, deterministically and for free."""
        session.start_flow(
            FlowName.PROPERTY_CREATION,
            now=NOW,
            first_step=FlowStep.COLLECT_MINIMUM_DURATION,
        )
        session.context["next_question"] = "ask.minimum_duration"
        session.context["property_type"] = "APARTMENT"
        session.context["location"] = {"city": "Yaoundé", "neighbourhood": "Bastos"}
        session.context["rent"] = 150_000

        model = StubModel(result=_model_result())
        use_case, _, usage, sessions = build(
            uow, session, message_id, text="5", model=model
        )

        report = await use_case.execute(
            AnalysisCommand(message_id=message_id, session_id=session.id), now=NOW
        )

        assert model.calls == 0
        assert usage.entries == []
        assert report.facts["minimum_duration_months"] == 5
        assert sessions.store[session.id].context["minimum_duration_months"] == 5


class TestRoleStability:
    async def test_a_listing_in_progress_is_not_reopened_as_a_search(
        self,
        uow: FakeUnitOfWork,
        session: ConversationSession,
        message_id: uuid.UUID,
    ) -> None:
        """Once a landlord has given facts, an ambiguous "je loue" read as a
        search must not throw them into the tenant flow and wipe the form."""
        session.start_flow(
            FlowName.PROPERTY_CREATION, now=NOW, first_step=FlowStep.COLLECT_PRICE
        )
        session.context["property_type"] = "APARTMENT"

        model = StubModel(result=_model_result(intent="PROPERTY_SEARCH", entities={}))
        use_case, _, _, sessions = build(
            uow, session, message_id, text="je loue", model=model
        )

        await use_case.execute(
            AnalysisCommand(message_id=message_id, session_id=session.id), now=NOW
        )

        saved = sessions.store[session.id]
        assert saved.flow is FlowName.PROPERTY_CREATION
        assert saved.context["property_type"] == "APARTMENT"

    async def test_a_first_message_still_opens_the_flow(
        self,
        uow: FakeUnitOfWork,
        session: ConversationSession,
        message_id: uuid.UUID,
    ) -> None:
        assert session.flow is FlowName.SUPPORT

        model = StubModel(result=_model_result(intent="CREATE_PROPERTY"))
        use_case, _, _, sessions = build(
            uow, session, message_id, text=AMBIGUOUS_TEXT, model=model
        )

        await use_case.execute(
            AnalysisCommand(message_id=message_id, session_id=session.id), now=NOW
        )

        assert sessions.store[session.id].flow is FlowName.PROPERTY_CREATION


class TestModelWrittenReply:
    async def test_the_model_sentence_is_what_gets_staged(
        self,
        uow: FakeUnitOfWork,
        session: ConversationSession,
        message_id: uuid.UUID,
    ) -> None:
        responder = StubResponseModel(text="Parfait, quel est le loyer mensuel ?")
        use_case, _, _, _ = build(
            uow,
            session,
            message_id,
            model=StubModel(result=_model_result()),
            response_model=responder,
        )

        await use_case.execute(
            AnalysisCommand(message_id=message_id, session_id=session.id), now=NOW
        )

        staged = uow.events.staged
        assert len(staged) == 1
        assert isinstance(staged[0], WhatsAppMessageSendRequested)
        assert staged[0].text == "Parfait, quel est le loyer mensuel ?"
        assert responder.calls == 1

    async def test_a_looped_fragment_is_collapsed(
        self,
        uow: FakeUnitOfWork,
        session: ConversationSession,
        message_id: uuid.UUID,
    ) -> None:
        """A decoding loop ("en sus en sus en sus") is sent once, not verbatim."""
        model = StubModel(
            result=_model_result(
                reply=(
                    "Le loyer est de 50 000 FCFA en sus en sus en sus "
                    "en plus du loyer."
                ),
            )
        )
        use_case, _, _, _ = build(
            uow, session, message_id, text=AMBIGUOUS_TEXT, model=model
        )

        await use_case.execute(
            AnalysisCommand(message_id=message_id, session_id=session.id), now=NOW
        )

        assert (
            uow.events.staged[0].text
            == "Le loyer est de 50 000 FCFA en sus en plus du loyer."
        )
        # Already the model's own sentence: nothing to say again.
        assert uow.events.staged[0].answering is None

    async def test_the_reply_prompt_is_told_what_to_ask(
        self,
        uow: FakeUnitOfWork,
        session: ConversationSession,
        message_id: uuid.UUID,
    ) -> None:
        session.start_flow(
            FlowName.PROPERTY_CREATION, now=NOW, first_step=FlowStep.COLLECT_PRICE
        )
        responder = StubResponseModel()
        use_case, _, _, _ = build(
            uow,
            session,
            message_id,
            model=StubModel(
                result=_model_result(
                    intent="COLLECT_PRICE",
                    entities={
                        "property_type": "APARTMENT",
                        "standing": "MODERN",
                        "quantity": 1,
                        "city": "Douala",
                        "neighbourhood": "Akwa",
                    },
                )
            ),
            response_model=responder,
        )

        await use_case.execute(
            AnalysisCommand(message_id=message_id, session_id=session.id), now=NOW
        )

        payload = json.loads(responder.prompts[0].user)
        assert payload["understood"]["accepted"] is True
        assert "rent" in payload["ask_about"]
        # A listing is the landlord's side: the wording must say so, otherwise
        # the model asks for "le loyer que vous recherchez" and reads as if the
        # platform had mistaken a landlord for a tenant.
        assert "landlord" in payload["you_are_helping"]
        assert "ask" in payload["ask_about"]

    async def test_the_reply_prompt_knows_the_tenant_side(
        self,
        uow: FakeUnitOfWork,
        session: ConversationSession,
        message_id: uuid.UUID,
    ) -> None:
        session.start_flow(
            FlowName.PROPERTY_SEARCH, now=NOW, first_step=FlowStep.COLLECT_PRICE
        )
        responder = StubResponseModel()
        use_case, _, _, _ = build(
            uow,
            session,
            message_id,
            model=StubModel(
                result=_model_result(
                    intent="COLLECT_PRICE",
                    entities={
                        "property_type": "APARTMENT",
                        "standing": "MODERN",
                        "quantity": 1,
                        "city": "Douala",
                    },
                )
            ),
            response_model=responder,
        )

        await use_case.execute(
            AnalysisCommand(message_id=message_id, session_id=session.id), now=NOW
        )

        payload = json.loads(responder.prompts[0].user)
        assert "tenant" in payload["you_are_helping"]
        assert "willing to pay" in payload["ask_about"]

    async def test_a_failed_reply_falls_back_to_the_template(
        self,
        uow: FakeUnitOfWork,
        session: ConversationSession,
        message_id: uuid.UUID,
    ) -> None:
        responder = StubResponseModel(error=ExternalServiceUnavailable("provider down"))
        use_case, _, _, _ = build(
            uow,
            session,
            message_id,
            model=StubModel(result=_model_result()),
            response_model=responder,
        )

        report = await use_case.execute(
            AnalysisCommand(message_id=message_id, session_id=session.id), now=NOW
        )

        assert report.accepted is True
        assert uow.events.staged[0].text is None

    async def test_the_reply_call_is_added_to_the_ledger(
        self,
        uow: FakeUnitOfWork,
        session: ConversationSession,
        message_id: uuid.UUID,
    ) -> None:
        use_case, _, usage, _ = build(
            uow,
            session,
            message_id,
            model=StubModel(result=_model_result(cost=0.0004)),
            response_model=StubResponseModel(cost=0.0001),
        )

        report = await use_case.execute(
            AnalysisCommand(message_id=message_id, session_id=session.id), now=NOW
        )

        assert usage.entries[0].cost_usd == Decimal("0.0005")
        assert report.cost_usd == Decimal("0.0005")

    async def test_the_fast_lane_does_not_write_a_reply(
        self,
        uow: FakeUnitOfWork,
        session: ConversationSession,
        message_id: uuid.UUID,
    ) -> None:
        """A bare amount is answered from the template: no second model call."""

        model = StubModel(result=_model_result())
        responder = StubResponseModel()
        use_case, _, _, _ = build(
            uow,
            session,
            message_id,
            text="250 000 fcfa",
            model=model,
            response_model=responder,
        )

        await use_case.execute(
            AnalysisCommand(message_id=message_id, session_id=session.id), now=NOW
        )

        assert model.calls == 0
        assert responder.calls == 0
        assert uow.events.staged[0].text is None


class TestRefusals:
    async def test_a_low_confidence_answer_does_not_move_the_conversation(
        self,
        uow: FakeUnitOfWork,
        session: ConversationSession,
        message_id: uuid.UUID,
    ) -> None:
        use_case, turns, _, sessions = build(
            uow,
            session,
            message_id,
            text="une phrase indeterminate",
            model=StubModel(result=_model_result(confidence=0.4)),
        )

        report = await use_case.execute(
            AnalysisCommand(message_id=message_id, session_id=session.id), now=NOW
        )

        assert report.accepted is False
        assert report.reason == "low_confidence"
        assert report.facts == {}
        assert session.context == {}
        assert sessions.saved == []
        assert turns.turns[0].confidence == 0.4

    async def test_an_invented_intent_is_not_obeyed(
        self,
        uow: FakeUnitOfWork,
        session: ConversationSession,
        message_id: uuid.UUID,
    ) -> None:
        use_case, _, _, sessions = build(
            uow,
            session,
            message_id,
            text="une phrase indeterminate",
            model=StubModel(result=_model_result(intent="TRANSFER_MONEY")),
        )

        report = await use_case.execute(
            AnalysisCommand(message_id=message_id, session_id=session.id), now=NOW
        )

        assert report.intent == "UNKNOWN"
        assert report.reason == "unsupported_intent"
        assert sessions.saved == []

    async def test_absurd_facts_are_reported_and_refused(
        self,
        uow: FakeUnitOfWork,
        session: ConversationSession,
        message_id: uuid.UUID,
    ) -> None:
        use_case, _, _, sessions = build(
            uow,
            session,
            message_id,
            text="une phrase indeterminate",
            model=StubModel(
                result=_model_result(entities={"property_type": "APARTMENT", "price": 10**13})
            ),
        )

        report = await use_case.execute(
            AnalysisCommand(message_id=message_id, session_id=session.id), now=NOW
        )

        assert "rent" in report.rejected_facts
        assert "rent" not in report.facts
        assert "rent" not in session.context
        assert sessions.saved

    async def test_a_model_proposing_a_string_number_is_survived(
        self,
        uow: FakeUnitOfWork,
        session: ConversationSession,
        message_id: uuid.UUID,
    ) -> None:
        """`{"price": "beaucoup"}` must not reach the value objects as a crash."""
        use_case, _, _, _ = build(
            uow,
            session,
            message_id,
            text="une phrase indeterminate",
            model=StubModel(result=_model_result(entities={"price": "beaucoup"})),
        )

        report = await use_case.execute(
            AnalysisCommand(message_id=message_id, session_id=session.id), now=NOW
        )

        assert report.rejected_facts.keys() == {"rent"}

    async def test_without_a_model_the_turn_is_recorded_as_unknown(
        self,
        uow: FakeUnitOfWork,
        session: ConversationSession,
        message_id: uuid.UUID,
    ) -> None:
        use_case, turns, _, _ = build(uow, session, message_id, text="une phrase indeterminate")

        report = await use_case.execute(
            AnalysisCommand(message_id=message_id, session_id=session.id), now=NOW
        )

        assert report.reason == "no_model_configured"
        assert len(turns.turns) == 1
        assert turns.turns[0].intent == "UNKNOWN"


class TestBudget:
    async def test_an_exhausted_daily_budget_blocks_the_call(
        self,
        uow: FakeUnitOfWork,
        session: ConversationSession,
        message_id: uuid.UUID,
    ) -> None:
        model = StubModel(result=_model_result())
        use_case, _, usage, _ = build(
            uow,
            session,
            message_id,
            model=model,
            ledger=FakeLedger(day_spend=Decimal("25.00"), user_spend=Decimal("0")),
            budget=BudgetPolicy(daily_usd=Decimal("25"), per_user_daily_usd=Decimal("0.25")),
        )

        report = await use_case.execute(
            AnalysisCommand(message_id=message_id, session_id=session.id), now=NOW
        )

        assert model.calls == 0
        assert report.reason == "daily_budget_exhausted"
        assert usage.entries == []

    async def test_an_exhausted_user_budget_blocks_the_call(
        self,
        uow: FakeUnitOfWork,
        session: ConversationSession,
        message_id: uuid.UUID,
    ) -> None:
        model = StubModel(result=_model_result())
        use_case, _, _, _ = build(
            uow,
            session,
            message_id,
            model=model,
            ledger=FakeLedger(day_spend=Decimal("1"), user_spend=Decimal("0.25")),
            budget=BudgetPolicy(daily_usd=Decimal("25"), per_user_daily_usd=Decimal("0.25")),
        )

        report = await use_case.execute(
            AnalysisCommand(message_id=message_id, session_id=session.id), now=NOW
        )

        assert model.calls == 0
        assert report.reason == "per_user_budget_exhausted"

    async def test_a_call_below_the_budget_is_allowed(
        self,
        uow: FakeUnitOfWork,
        session: ConversationSession,
        message_id: uuid.UUID,
    ) -> None:
        model = StubModel(result=_model_result())
        use_case, _, _, _ = build(
            uow,
            session,
            message_id,
            model=model,
            ledger=FakeLedger(day_spend=Decimal("1"), user_spend=Decimal("0.01")),
            budget=BudgetPolicy(daily_usd=Decimal("25"), per_user_daily_usd=Decimal("0.25")),
        )

        report = await use_case.execute(
            AnalysisCommand(message_id=message_id, session_id=session.id), now=NOW
        )

        assert model.calls == 1
        assert report.used_llm is True


class TestMissingInputs:
    async def test_an_unknown_message_writes_nothing(
        self, uow: FakeUnitOfWork, session: ConversationSession
    ) -> None:
        turns = FakeTurnRepository()
        use_case = AnalyseConversationMessageUseCase(
            uow,
            inbound=FakeInboundRepository(),
            sessions=FakeSessionRepository(),
            turns=turns,
            usage=FakeLedger(),
        )

        report = await use_case.execute(
            AnalysisCommand(message_id=uuid.uuid4(), session_id=session.id), now=NOW
        )

        assert report.reason == "message_not_found"
        assert turns.turns == []
        assert uow.commits == 0

    async def test_an_unknown_session_writes_nothing(
        self, uow: FakeUnitOfWork, session: ConversationSession, message_id: uuid.UUID
    ) -> None:
        turns = FakeTurnRepository()
        use_case = AnalyseConversationMessageUseCase(
            uow,
            inbound=FakeInboundRepository(
                rows={
                    message_id: StoredInboundMessage(
                        message_id=message_id,
                        session_id=session.id,
                        sender_user_id=None,
                        processed=False,
                        text="250 000 fcfa",
                    )
                }
            ),
            sessions=FakeSessionRepository(),
            turns=turns,
            usage=FakeLedger(),
        )

        report = await use_case.execute(
            AnalysisCommand(message_id=message_id, session_id=uuid.uuid4()), now=NOW
        )

        assert report.reason == "session_not_found"
        assert turns.turns == []


class TestConcurrency:
    async def test_a_concurrent_advance_is_not_overwritten(
        self, uow: FakeUnitOfWork, session: ConversationSession, message_id: uuid.UUID
    ) -> None:
        """Two workers analysing the same session must not silently clobber the
        step: the second one loses loudly and gets redelivered."""
        use_case, _, _, sessions = build(
            uow,
            session,
            message_id,
            text="250 000 fcfa",
            sessions=FakeSessionRepository(
                store={session.id: session}, conflict_on_save=True
            ),
        )

        with pytest.raises(ConcurrencyConflict):
            await use_case.execute(
                AnalysisCommand(message_id=message_id, session_id=session.id), now=NOW
            )

        assert sessions.saved

    async def test_a_revision_changed_by_another_worker_is_refused(
        self, uow: FakeUnitOfWork, session: ConversationSession, message_id: uuid.UUID
    ) -> None:
        """The entity's own guard, exercised through the pipeline: the observed
        revision is the one read at load time."""
        stale = ConversationSession(
            session_id=uuid.uuid4(),
            user_id=uuid.uuid4(),
            phone=PhoneNumber.normalize("+237699000000"),
            now=NOW,
        )
        stale.revision = 3
        use_case, _, _, sessions = build(
            uow, stale, message_id, text="250 000 fcfa"
        )

        sessions.store[stale.id] = stale
        # A worker that moved the session while this one was reading it.
        stale.revision = 9
        original_save = sessions.save

        async def save(
            session: ConversationSession, *, expected_revision: int | None = None
        ) -> None:
            if expected_revision != 3:
                raise InvalidStateTransition(
                    "conversation session was modified concurrently",
                    context={"session_id": str(session.id)},
                )
            await original_save(session, expected_revision=expected_revision)

        sessions.save = save  # type: ignore[method-assign]

        with pytest.raises((ConcurrencyConflict, InvalidStateTransition)):
            await use_case.execute(
                AnalysisCommand(message_id=message_id, session_id=stale.id), now=NOW
            )


class TestSessionAdvancement:
    async def test_the_step_follows_the_missing_fields(
        self, uow: FakeUnitOfWork, session: ConversationSession, message_id: uuid.UUID
    ) -> None:
        use_case, _, _, _ = build(
            uow, session, message_id, text="une phrase indeterminate",
            model=StubModel(
                result=_model_result(
                    entities={
                        "property_type": "APARTMENT",
                        "standing": "MODERN",
                        "quantity": 1,
                        "city": "Douala",
                        "neighbourhood": "Akwa",
                    }
                )
            ),
        )

        report = await use_case.execute(
            AnalysisCommand(message_id=message_id, session_id=session.id), now=NOW
        )

        assert session.step is FlowStep.COLLECT_PRICE
        assert report.next_step == "COLLECT_PRICE"
        assert report.question_key == "ask.rent"
        assert session.context["next_question"] == "ask.rent"

    async def test_a_local_shorthand_amount_overrides_the_model(
        self, uow: FakeUnitOfWork, session: ConversationSession, message_id: uuid.UUID
    ) -> None:
        """The model reads "50 mil" as fifty million; the code knows it is fifty
        thousand, and the rule wins over the guess."""
        use_case, _, _, _ = build(
            uow,
            session,
            message_id,
            text="le loyer est 50 mil",
            model=StubModel(
                result=_model_result(
                    entities={"property_type": "STUDIO", "price": 50_000_000}
                )
            ),
        )

        await use_case.execute(
            AnalysisCommand(message_id=message_id, session_id=session.id), now=NOW
        )

        assert session.context["rent"] == 50_000

    async def test_facts_already_in_the_session_advance_the_form(
        self, uow: FakeUnitOfWork, session: ConversationSession, message_id: uuid.UUID
    ) -> None:
        """The landlord already named the studio; giving the city next must move
        to the rent question instead of restarting at the property type."""
        session.start_flow(
            FlowName.PROPERTY_CREATION, now=NOW, first_step=FlowStep.COLLECT_LOCATION
        )
        session.context["property_type"] = "STUDIO"
        session.context["standing"] = "MODERN"
        session.context["quantity"] = 1

        use_case, _, _, _ = build(
            uow,
            session,
            message_id,
            text="Cameroun Yaoundé, Bastos",
            model=StubModel(
                result=_model_result(
                    intent="COLLECT_LOCATION",
                    entities={"city": "Yaoundé", "neighbourhood": "Bastos"},
                )
            ),
        )

        report = await use_case.execute(
            AnalysisCommand(message_id=message_id, session_id=session.id), now=NOW
        )

        assert report.next_step == "COLLECT_PRICE"
        assert report.question_key == "ask.rent"
        assert session.step is FlowStep.COLLECT_PRICE
        assert session.context["property_type"] == "STUDIO"

    async def test_a_complete_listing_asks_for_confirmation(
        self, uow: FakeUnitOfWork, session: ConversationSession, message_id: uuid.UUID
    ) -> None:
        use_case, _, _, _ = build(
            uow,
            session,
            message_id,
            text="une phrase indeterminate",
            model=StubModel(
                result=_model_result(
                    entities={
                        "property_type": "APARTMENT",
                        "standing": "MODERN",
                        "quantity": 1,
                        "city": "Douala",
                        "neighbourhood": "Akwa",
                        "price": 250_000,
                        "charges": 0,
                        "charging_policy": "INCLUDED",
                        "deposit_months": 2,
                        "advance_months": 6,
                        "amenities": {"security": True, "parking": False},
                    }
                )
            ),
        )

        report = await use_case.execute(
            AnalysisCommand(message_id=message_id, session_id=session.id), now=NOW
        )

        assert session.step is FlowStep.CONFIRM_PROPERTY
        assert report.next_step == "CONFIRM_PROPERTY"

    async def test_the_entry_conditions_are_asked_before_confirmation(
        self, uow: FakeUnitOfWork, session: ConversationSession, message_id: uuid.UUID
    ) -> None:
        """A listing that only has a type, a place and a rent is not done: the
        move-in cost the quality gate needs would otherwise be reported missing
        only after the landlord already confirmed."""
        use_case, _, _, _ = build(
            uow,
            session,
            message_id,
            text="une phrase indeterminate",
            model=StubModel(
                result=_model_result(
                    entities={
                        "property_type": "APARTMENT",
                        "standing": "MODERN",
                        "quantity": 1,
                        "city": "Douala",
                        "neighbourhood": "Akwa",
                        "price": 250_000,
                    }
                )
            ),
        )

        report = await use_case.execute(
            AnalysisCommand(message_id=message_id, session_id=session.id), now=NOW
        )

        assert report.next_step == "COLLECT_CHARGES"
        assert report.question_key == "ask.charges"

    async def test_a_bare_quartier_closes_the_location_question(
        self, uow: FakeUnitOfWork, session: ConversationSession, message_id: uuid.UUID
    ) -> None:
        """The landlord answers "Bastos", not "Yaoundé, Bastos". The district
        index resolves the city, so the form advances instead of looping on the
        location question forever."""
        session.start_flow(
            FlowName.PROPERTY_CREATION, now=NOW, first_step=FlowStep.COLLECT_LOCATION
        )
        session.context["property_type"] = "STUDIO"
        session.context["standing"] = "MODERN"
        session.context["quantity"] = 1
        session.context["next_question"] = "ask.location"

        use_case, _, _, _ = build(
            uow,
            session,
            message_id,
            text="Bastos",
            model=StubModel(
                result=_model_result(
                    intent="COLLECT_LOCATION", entities={"neighbourhood": "Bastos"}
                )
            ),
        )

        report = await use_case.execute(
            AnalysisCommand(message_id=message_id, session_id=session.id), now=NOW
        )

        assert session.context["location"]["neighbourhood"] == "Bastos"
        assert session.context["location"]["city"] == "Yaoundé"
        assert report.next_step == "COLLECT_PRICE"

    async def test_the_revision_is_bumped_when_state_moves(
        self, uow: FakeUnitOfWork, session: ConversationSession, message_id: uuid.UUID
    ) -> None:
        before = session.revision
        use_case, _, _, _ = build(uow, session, message_id, text="250 000 fcfa")

        await use_case.execute(
            AnalysisCommand(message_id=message_id, session_id=session.id), now=NOW
        )

        assert session.revision > before


class TestTheStagedReply:
    """A turn that was understood must be a turn that answers.

    The reply is staged on the same unit of work as the conversation state, so
    an analysis that lands without an answer cannot happen by construction.
    """

    async def test_a_question_turn_requests_exactly_one_reply(
        self, uow: FakeUnitOfWork, session: ConversationSession, message_id: uuid.UUID
    ) -> None:
        use_case, _, _, _ = build(
            uow,
            session,
            message_id,
            text="une phrase indeterminate",
            model=StubModel(
                result=_model_result(
                    entities={
                        "property_type": "APARTMENT",
                        "standing": "MODERN",
                        "quantity": 1,
                        "city": "Douala",
                        "neighbourhood": "Akwa",
                    }
                )
            ),
        )

        report = await use_case.execute(
            AnalysisCommand(message_id=message_id, session_id=session.id), now=NOW
        )

        assert report.question_key == "ask.rent"
        staged = uow.events.staged
        assert len(staged) == 1
        reply = staged[0]
        assert isinstance(reply, WhatsAppMessageSendRequested)
        assert reply.reply_kind == "QUESTION"
        assert reply.question_key == "ask.rent"
        assert reply.recipient_phone == PHONE
        assert reply.session_id == session.id

    async def test_a_refused_analysis_answers_with_a_clarification(
        self, uow: FakeUnitOfWork, session: ConversationSession, message_id: uuid.UUID
    ) -> None:
        use_case, _, _, _ = build(
            uow, session, message_id, model=StubModel(result=_model_result(confidence=0.4))
        )

        report = await use_case.execute(
            AnalysisCommand(message_id=message_id, session_id=session.id), now=NOW
        )

        assert report.accepted is False
        staged = uow.events.staged
        assert len(staged) == 1
        reply = staged[0]
        assert isinstance(reply, WhatsAppMessageSendRequested)
        assert reply.reply_kind == "CLARIFY"
        assert reply.question_key is None

    async def test_a_turn_without_a_question_asks_for_nothing_more(
        self, uow: FakeUnitOfWork, session: ConversationSession, message_id: uuid.UUID
    ) -> None:
        use_case, _, _, _ = build(
            uow,
            session,
            message_id,
            text="une phrase indeterminate",
            model=StubModel(result=_model_result(intent="SUPPORT")),
        )

        report = await use_case.execute(
            AnalysisCommand(message_id=message_id, session_id=session.id), now=NOW
        )

        assert report.question_key is None
        staged = uow.events.staged
        assert len(staged) == 1
        assert staged[0].reply_kind == "ACK"

    async def test_an_unmatched_message_requests_no_reply(
        self, uow: FakeUnitOfWork, session: ConversationSession
    ) -> None:
        use_case = AnalyseConversationMessageUseCase(
            uow,
            inbound=FakeInboundRepository(),
            sessions=FakeSessionRepository(),
            turns=FakeTurnRepository(),
            usage=FakeLedger(),
        )

        await use_case.execute(
            AnalysisCommand(message_id=uuid.uuid4(), session_id=session.id), now=NOW
        )

        assert uow.events.staged == []
        assert uow.commits == 0


class TestSingleCallReply:
    """One round-trip answers the message and phrases the answer.

    The reply is only asked for a second time when the model left it empty, so a
    provider that inlines it halves the latency without changing any guarantee.
    """

    async def test_an_inline_reply_avoids_the_second_call(
        self,
        uow: FakeUnitOfWork,
        session: ConversationSession,
        message_id: uuid.UUID,
    ) -> None:
        responder = StubResponseModel()
        use_case, _, _, _ = build(
            uow,
            session,
            message_id,
            model=StubModel(
                result=_model_result(reply="Parfait, quel est le loyer mensuel ?")
            ),
            response_model=responder,
        )

        report = await use_case.execute(
            AnalysisCommand(message_id=message_id, session_id=session.id), now=NOW
        )

        assert report.accepted is True
        assert responder.calls == 0
        staged = uow.events.staged
        assert staged[0].text == "Parfait, quel est le loyer mensuel ?"

    async def test_an_empty_inline_reply_falls_back_to_the_writer(
        self,
        uow: FakeUnitOfWork,
        session: ConversationSession,
        message_id: uuid.UUID,
    ) -> None:
        responder = StubResponseModel()
        use_case, _, _, _ = build(
            uow,
            session,
            message_id,
            model=StubModel(result=_model_result(reply=None)),
            response_model=responder,
        )

        await use_case.execute(
            AnalysisCommand(message_id=message_id, session_id=session.id), now=NOW
        )

        assert responder.calls == 1

    async def test_an_inline_reply_that_moved_past_the_open_step_is_rewritten(
        self,
        uow: FakeUnitOfWork,
        session: ConversationSession,
        message_id: uuid.UUID,
    ) -> None:
        """Observed live: a landlord's "elle sont inclus" was understood but
        produced no charges fact, so the session kept the charges question open
        while the inline writer asked the deposit. His next "aucune", meant for
        the minimum duration, was then filed as "no charges"."""
        session.start_flow(
            FlowName.PROPERTY_CREATION, now=NOW, first_step=FlowStep.COLLECT_CHARGES
        )
        session.context.update(
            {
                "property_type": "ROOM",
                "standing": "MODERN",
                "quantity": 1,
                "location": {"city": "Douala", "neighbourhood": "Mvan"},
                "rent": 30_000,
                "next_question": "ask.charges",
            }
        )
        inline = (
            "D'accord, les charges sont incluses. Combien de mois de caution "
            "remboursable demandez-vous ?"
        )
        responder = StubResponseModel(
            text="L'eau et l'électricité sont-elles incluses dans le loyer ?"
        )
        use_case, turns, _, _ = build(
            uow,
            session,
            message_id,
            text="elle sont inclus",
            model=StubModel(
                result=_model_result(
                    intent="COLLECT_CHARGES", entities={}, reply=inline
                )
            ),
            response_model=responder,
        )

        report = await use_case.execute(
            AnalysisCommand(message_id=message_id, session_id=session.id), now=NOW
        )

        # The decided question wins, not the sentence the writer moved on to.
        assert report.question_key == "ask.charges"
        assert session.context["next_question"] == "ask.charges"
        assert responder.calls == 1
        assert uow.events.staged[0].text == responder.text
        assert uow.events.staged[0].text != inline
        assert turns.turns[0].used_llm is True

    async def test_an_inline_reply_is_kept_when_the_turn_recorded_a_field(
        self,
        uow: FakeUnitOfWork,
        session: ConversationSession,
        message_id: uuid.UUID,
    ) -> None:
        """The guard is the *absence* of a recorded field, not the step: a turn
        that really advanced keeps the model's sentence and its second call."""
        session.start_flow(
            FlowName.PROPERTY_CREATION, now=NOW, first_step=FlowStep.COLLECT_CHARGES
        )
        session.context.update(
            {
                "property_type": "ROOM",
                "standing": "MODERN",
                "quantity": 1,
                "location": {"city": "Douala", "neighbourhood": "Mvan"},
                "rent": 30_000,
                "next_question": "ask.charges",
            }
        )
        responder = StubResponseModel()
        use_case, _, _, _ = build(
            uow,
            session,
            message_id,
            text="eau et électricité incluses",
            model=StubModel(
                result=_model_result(
                    intent="COLLECT_CHARGES",
                    entities={"charges_included": True},
                    reply="Parfait, charges incluses. Combien de mois de caution ?",
                )
            ),
            response_model=responder,
        )

        await use_case.execute(
            AnalysisCommand(message_id=message_id, session_id=session.id), now=NOW
        )

        assert responder.calls == 0
        assert uow.events.staged[0].text == (
            "Parfait, charges incluses. Combien de mois de caution ?"
        )

    async def test_an_inline_reply_asking_another_field_is_rewritten(
        self,
        uow: FakeUnitOfWork,
        session: ConversationSession,
        message_id: uuid.UUID,
    ) -> None:
        """The writer names the field it asked about. Here it moved past the rent
        to the charges while the session kept the rent open, so its sentence is
        written again against the rent the backend decided on."""
        session.start_flow(
            FlowName.PROPERTY_CREATION, now=NOW, first_step=FlowStep.COLLECT_PRICE
        )
        session.context.update(
            {
                "property_type": "ROOM",
                "standing": "MODERN",
                "quantity": 1,
                "location": {"city": "Douala", "neighbourhood": "Mvan"},
                "next_question": "ask.rent",
            }
        )
        inline = "Très bien. Les charges sont-elles incluses dans le loyer ?"
        responder = StubResponseModel(text="Quel loyer mensuel demandez-vous ?")
        use_case, _, _, _ = build(
            uow,
            session,
            message_id,
            text="c'est une chambre",
            model=StubModel(
                result=_model_result(
                    intent="COLLECT_PRICE",
                    entities={"property_type": "ROOM"},
                    reply=inline,
                    asking="charges",
                )
            ),
            response_model=responder,
        )

        report = await use_case.execute(
            AnalysisCommand(message_id=message_id, session_id=session.id), now=NOW
        )

        assert report.question_key == "ask.rent"
        assert responder.calls == 1
        assert uow.events.staged[0].text == responder.text
        assert uow.events.staged[0].text != inline

    async def test_the_analysis_prompt_carries_the_form_and_price_context(
        self,
        uow: FakeUnitOfWork,
        session: ConversationSession,
        message_id: uuid.UUID,
    ) -> None:
        """The single call can only ask the right next question and read a bare
        number if the prompt hands it the ordered form and the local rents."""
        session.start_flow(
            FlowName.PROPERTY_CREATION, now=NOW, first_step=FlowStep.COLLECT_PROPERTY_TYPE
        )
        model = StubModel(result=_model_result())
        use_case, _, _, _ = build(uow, session, message_id, model=model)

        await use_case.execute(
            AnalysisCommand(message_id=message_id, session_id=session.id), now=NOW
        )

        payload = json.loads(model.prompts[0].user)
        assert [f["field"] for f in payload["required_fields"]] == [
            "property_type",
            "standing",
            "quantity",
            "location",
            "rent",
            "charges",
            "deposit_months",
            "advance_months",
            "security",
        ]
        studio = payload["price_reference_monthly_xaf"]["STUDIO"]
        assert studio["min"] < studio["max"]
        assert "reply" in payload["schema"]["properties"]
        assert "standing" in payload["schema"]["properties"]["entities"]["properties"]
        assert payload["you_are_helping"]

    async def test_a_greeting_outside_any_task_is_shown_no_form(
        self,
        uow: FakeUnitOfWork,
        session: ConversationSession,
        message_id: uuid.UUID,
    ) -> None:
        """Observed live: shown the landlord's fields, "Hello comment tu vas"
        was answered "quel type de propriété tu veux proposer ?"."""
        model = StubModel(result=_model_result(intent="SUPPORT", entities={}))
        use_case, _, _, _ = build(
            uow, session, message_id, text="Hello comment tu vas", model=model
        )

        await use_case.execute(
            AnalysisCommand(message_id=message_id, session_id=session.id), now=NOW
        )

        assert json.loads(model.prompts[0].user)["required_fields"] == []
        assert "never assume whether they rent out or rent" in model.prompts[0].system

    async def test_the_prompt_omits_context_that_can_no_longer_be_used(
        self,
        uow: FakeUnitOfWork,
        session: ConversationSession,
        message_id: uuid.UUID,
    ) -> None:
        """Every turn re-sends the prompt, so fields that can no longer change the
        answer are pure cost: the allowlist lives once in the schema, the price
        band is useless once the rent is known, and collected fields are not
        re-listed as required."""
        session.start_flow(
            FlowName.PROPERTY_CREATION, now=NOW, first_step=FlowStep.COLLECT_LOCATION
        )
        session.context["property_type"] = "APARTMENT"
        session.context["standing"] = "MODERN"
        session.context["quantity"] = 1
        session.context["rent"] = 250_000
        session.context["next_question"] = "ask.location"
        model = StubModel(result=_model_result(intent="COLLECT_LOCATION"))

        use_case, _, _, _ = build(uow, session, message_id, text="Bastos", model=model)
        await use_case.execute(
            AnalysisCommand(message_id=message_id, session_id=session.id), now=NOW
        )

        payload = json.loads(model.prompts[0].user)
        assert "allowed_intents" not in payload
        assert "price_reference_monthly_xaf" not in payload
        fields = [f["field"] for f in payload["required_fields"]]
        assert "property_type" not in fields
        assert "rent" not in fields
        assert "location" in fields
        assert "next_question" not in payload["conversation"]["known_facts"]


# Where the gazetteer puts Bastos: a typed district carries its centre.
BASTOS_CENTRE = (3.894, 11.5107)


class StubSearch:
    """The catalogue behind the port: what it was asked, and what it holds."""

    def __init__(self, hits: list[ConversationPropertyHit] | None = None) -> None:
        self.hits = hits or []
        self.queries: list[ConversationSearchQuery] = []

    async def search_for_conversation(
        self, query: ConversationSearchQuery, *, limit: int = 5
    ) -> list[ConversationPropertyHit]:
        self.queries.append(query)
        return self.hits


class StubDemands:
    """The demand store behind the port: what it was asked to keep."""

    def __init__(self, *, stored: bool = True) -> None:
        self.stored = stored
        self.recorded: list[tuple[uuid.UUID, ConversationSearchQuery]] = []

    async def record(
        self, *, tenant_id: uuid.UUID, query: ConversationSearchQuery, now: datetime
    ) -> bool:
        self.recorded.append((tenant_id, query))
        return self.stored


class TestConversationSearch:
    """The tenant's criteria become a recorded demand, and the thread says so.

    Kora answers no search in the thread: a direct match, a neighbouring area or
    a price a little above all need preparing. The acknowledgement is platform
    text — it is a promise, and the model's sentence may not replace it.
    """

    async def test_complete_criteria_record_the_demand_and_acknowledge_it(
        self, uow: FakeUnitOfWork, session: ConversationSession, message_id: uuid.UUID
    ) -> None:
        search = StubSearch()
        demands = StubDemands()
        use_case, _, _, _ = build(
            uow,
            session,
            message_id,
            model=StubModel(
                result=_model_result(
                    intent="PROPERTY_SEARCH",
                    entities={
                        "property_type": "APARTMENT",
                        "city": "Yaoundé",
                        "neighbourhood": "Bastos",
                        "price": 180_000,
                        "advance_months": 3,
                    },
                    reply="Voilà ce que j'ai trouvé.",
                )
            ),
            search=search,
            demands=demands,
        )

        report = await use_case.execute(
            AnalysisCommand(message_id=message_id, session_id=session.id), now=NOW
        )

        assert demands.recorded == [
            (
                session.user_id,
                ConversationSearchQuery(
                    city="Yaoundé",
                    neighbourhoods=("Bastos",),
                    property_types=("APARTMENT",),
                    budget_xaf=180_000,
                    near=BASTOS_CENTRE,
                    near_exact=False,
                    advance_months=3,
                ),
            )
        ]
        # Nothing is printed from the catalogue in the thread any more.
        assert search.queries == []
        assert report.next_step == "AWAITING_MATCH"
        assert report.question_key is None
        assert session.flow is FlowName.PROPERTY_SEARCH
        assert session.step is FlowStep.AWAITING_MATCH

        staged = uow.events.staged
        assert len(staged) == 1
        # The promise wins over the sentence the model wrote, which here even
        # claims results that were never looked up.
        assert staged[0].text == (
            "Votre demande est enregistrée. Si un logement correspond à ce que "
            "vous cherchez, je reviens vers vous avec les informations."
        )
        assert staged[0].reply_kind == "ACK"

    async def test_the_next_search_question_is_worded_for_a_tenant(
        self, uow: FakeUnitOfWork, session: ConversationSession, message_id: uuid.UUID
    ) -> None:
        """The same step asks a landlord what rent they want and a tenant what
        budget they have: the key, not the step, carries the difference."""
        session.start_flow(
            FlowName.PROPERTY_SEARCH, now=NOW, first_step=FlowStep.COLLECT_LOCATION
        )
        session.context["property_type"] = "STUDIO"
        session.context["next_question"] = "ask.search_type"
        model = StubModel(
            result=_model_result(
                intent="COLLECT_LOCATION", entities={"city": "Douala"}
            )
        )

        use_case, _, _, _ = build(
            uow, session, message_id, text="Douala", model=model, search=StubSearch()
        )

        report = await use_case.execute(
            AnalysisCommand(message_id=message_id, session_id=session.id), now=NOW
        )

        assert report.next_step == "COLLECT_PRICE"
        assert report.question_key == "ask.search_budget"
        assert session.context["next_question"] == "ask.search_budget"
        staged = uow.events.staged
        assert staged[0].reply_kind == "QUESTION"
        assert staged[0].question_key == "ask.search_budget"

    async def test_the_acknowledgement_speaks_the_language_of_the_conversation(
        self, uow: FakeUnitOfWork, session: ConversationSession, message_id: uuid.UUID
    ) -> None:
        use_case, _, _, _ = build(
            uow,
            session,
            message_id,
            model=StubModel(
                result=_model_result(
                    language="en",
                    intent="PROPERTY_SEARCH",
                    entities={
                        "property_type": "APARTMENT",
                        "city": "Yaoundé",
                        "neighbourhood": "Bastos",
                        "price": 180_000,
                        "advance_months": 3,
                    },
                )
            ),
            demands=StubDemands(),
        )

        await use_case.execute(
            AnalysisCommand(message_id=message_id, session_id=session.id), now=NOW
        )

        assert session.language == "en"
        assert uow.events.staged[0].text == (
            "Your request is recorded. If a property matches what you are looking "
            "for, I will come back to you with the details."
        )

    async def test_a_bare_amount_is_acknowledged_in_the_remembered_language(
        self, uow: FakeUnitOfWork, session: ConversationSession, message_id: uuid.UUID
    ) -> None:
        """A bare "3" cannot tell its own language: the session's remembered
        one — read from an earlier English message — has to carry the answer.
        Bound to the advance by the open question, not to a rent of 3 francs."""
        session.language = "en"
        session.start_flow(
            FlowName.PROPERTY_SEARCH, now=NOW, first_step=FlowStep.COLLECT_ADVANCE
        )
        session.context["property_type"] = "STUDIO"
        session.context["location"] = {"city": "Yaoundé", "neighbourhood": "Odza"}
        session.context["rent"] = 50_000
        session.context["next_question"] = "ask.search_advance"
        demands = StubDemands()

        use_case, _, _, _ = build(uow, session, message_id, text="3", demands=demands)

        await use_case.execute(
            AnalysisCommand(message_id=message_id, session_id=session.id), now=NOW
        )

        assert demands.recorded[0][1].budget_xaf == 50_000
        assert demands.recorded[0][1].advance_months == 3
        assert uow.events.staged[0].text.startswith("Your request is recorded.")

    async def test_the_budget_is_followed_by_the_advance_question(
        self, uow: FakeUnitOfWork, session: ConversationSession, message_id: uuid.UUID
    ) -> None:
        """§3 asks a tenant four things; the months he can pay up front is the
        fourth, and no demand is recorded before it is known."""
        session.start_flow(
            FlowName.PROPERTY_SEARCH, now=NOW, first_step=FlowStep.COLLECT_PRICE
        )
        session.context["property_type"] = "STUDIO"
        session.context["location"] = {"city": "Yaoundé", "neighbourhood": "Odza"}
        session.context["next_question"] = "ask.search_budget"
        demands = StubDemands()

        use_case, _, _, _ = build(
            uow,
            session,
            message_id,
            text="50 000",
            model=StubModel(
                result=_model_result(intent="COLLECT_PRICE", entities={"price": 50_000})
            ),
            demands=demands,
        )

        await use_case.execute(
            AnalysisCommand(message_id=message_id, session_id=session.id), now=NOW
        )

        assert demands.recorded == []
        assert session.step is FlowStep.COLLECT_ADVANCE
        assert uow.events.staged[0].question_key == "ask.search_advance"

    async def test_the_tenant_form_is_what_the_model_is_given(
        self, uow: FakeUnitOfWork, session: ConversationSession, message_id: uuid.UUID
    ) -> None:
        """The model must never be shown the landlord's remaining fields during
        a search: charges and deposit are questions a tenant cannot answer."""
        session.start_flow(
            FlowName.PROPERTY_SEARCH, now=NOW, first_step=FlowStep.COLLECT_LOCATION
        )
        session.context["property_type"] = "APARTMENT"
        session.context["next_question"] = "ask.search_location"
        model = StubModel(
            result=_model_result(
                intent="COLLECT_LOCATION", entities={"city": "Douala"}
            )
        )

        use_case, _, _, _ = build(
            uow, session, message_id, text="Douala", model=model, search=StubSearch()
        )

        await use_case.execute(
            AnalysisCommand(message_id=message_id, session_id=session.id), now=NOW
        )

        payload = json.loads(model.prompts[0].user)
        assert [field["field"] for field in payload["required_fields"]] == [
            "location",
            "rent",
            "advance_months",
        ]
        assert "tenant" in payload["you_are_helping"]

    async def test_a_demand_that_cannot_be_stored_promises_nothing(
        self, uow: FakeUnitOfWork, session: ConversationSession, message_id: uuid.UUID
    ) -> None:
        """Nothing was kept, so nobody can come back to him: the thread says the
        search failed and goes back to the criteria instead of acknowledging."""
        session.start_flow(
            FlowName.PROPERTY_SEARCH, now=NOW, first_step=FlowStep.COLLECT_ADVANCE
        )
        session.context["property_type"] = "STUDIO"
        session.context["location"] = {"city": "Yaoundé", "neighbourhood": "Odza"}
        session.context["rent"] = 50_000
        session.context["next_question"] = "ask.search_advance"

        use_case, _, _, _ = build(
            uow, session, message_id, text="3 mois", demands=StubDemands(stored=False)
        )

        report = await use_case.execute(
            AnalysisCommand(message_id=message_id, session_id=session.id), now=NOW
        )

        assert report.next_step == "COLLECT_SEARCH_CRITERIA"
        assert session.step is FlowStep.COLLECT_SEARCH_CRITERIA
        text = uow.events.staged[0].text
        assert "Je n'ai pas pu lancer la recherche" in text
        assert "enregistrée" not in text

    async def test_a_search_without_a_port_still_answers_the_thread(
        self, uow: FakeUnitOfWork, session: ConversationSession, message_id: uuid.UUID
    ) -> None:
        """A deployment that never wired the demand store must fail as a
        sentence, not as a silence or a promise nobody can keep."""
        use_case, _, _, _ = build(
            uow,
            session,
            message_id,
            model=StubModel(
                result=_model_result(
                    intent="PROPERTY_SEARCH",
                    entities={
                        "property_type": "STUDIO",
                        "city": "Douala",
                        "price": 80_000,
                        "advance_months": 3,
                    },
                )
            ),
        )

        report = await use_case.execute(
            AnalysisCommand(message_id=message_id, session_id=session.id), now=NOW
        )

        assert report.accepted is True
        staged = uow.events.staged
        assert len(staged) == 1
        assert "recherche" in staged[0].text
        assert "enregistrée" not in staged[0].text
        assert staged[0].question_key == "ask.search_results"

    async def test_without_a_model_the_rules_still_record_the_demand(
        self, uow: FakeUnitOfWork, session: ConversationSession, message_id: uuid.UUID
    ) -> None:
        """The deployment with no AI key must still answer a tenant: the rules
        read the sentence, the domain validates it, the demand is kept."""
        demands = StubDemands()

        use_case, _, _, _ = build(
            uow,
            session,
            message_id,
            text="je cherche un appartement à Bastos pour 150 000 fcfa",
            demands=demands,
        )

        report = await use_case.execute(
            AnalysisCommand(message_id=message_id, session_id=session.id), now=NOW
        )

        assert report.accepted is True
        assert report.used_llm is False
        # Three of the four answers in one sentence: only the advance is asked.
        assert demands.recorded == []
        assert session.step is FlowStep.COLLECT_ADVANCE
        assert uow.events.staged[0].question_key == "ask.search_advance"

    async def test_every_accepted_neighbourhood_reaches_the_demand(
        self, uow: FakeUnitOfWork, session: ConversationSession, message_id: uuid.UUID
    ) -> None:
        demands = StubDemands()
        use_case, _, _, _ = build(
            uow,
            session,
            message_id,
            model=StubModel(
                result=_model_result(
                    intent="PROPERTY_SEARCH",
                    entities={
                        "property_type": "STUDIO",
                        "city": "Douala",
                        "neighbourhood": "Bonamoussadi",
                        "neighbourhoods": ["Bonamoussadi", "Makepe"],
                        "price": 60_000,
                        "advance_months": 3,
                    },
                )
            ),
            demands=demands,
        )

        await use_case.execute(
            AnalysisCommand(message_id=message_id, session_id=session.id), now=NOW
        )

        assert demands.recorded[0][1].neighbourhoods == ("Bonamoussadi", "Makepe")

    async def test_a_follow_up_while_waiting_gets_the_criteria_and_no_promise(
        self, uow: FakeUnitOfWork, session: ConversationSession, message_id: uuid.UUID
    ) -> None:
        """Left to the model, "tu me reviens quand ?" was answered "dès que
        possible", and "can I visit this weekend?" with a visit nobody set up."""
        session.start_flow(
            FlowName.PROPERTY_SEARCH, now=NOW, first_step=FlowStep.AWAITING_MATCH
        )
        session.context.update(
            {
                "property_type": "STUDIO",
                "location": {
                    "city": "Douala",
                    "neighbourhood": "Bonamoussadi",
                    "neighbourhoods": ["Bonamoussadi", "Makepe"],
                },
                "rent": 60_000,
            }
        )
        demands = StubDemands()
        use_case, _, _, _ = build(
            uow,
            session,
            message_id,
            text="Mais tu me reviens quand ?",
            model=StubModel(
                result=_model_result(
                    intent="AVAILABILITY_CHECK",
                    entities={},
                    reply="Je vous reviens dès que possible.",
                )
            ),
            demands=demands,
        )

        report = await use_case.execute(
            AnalysisCommand(message_id=message_id, session_id=session.id), now=NOW
        )

        assert demands.recorded == []
        assert report.question_key is None
        assert session.step is FlowStep.AWAITING_MATCH
        staged = uow.events.staged[0]
        assert staged.reply_kind == "ACK"
        assert staged.text == (
            "Votre recherche est bien enregistrée : Studio - Bonamoussadi ou Makepe, "
            "Douala - 60 000 FCFA/mois maximum. Je n'ai pas encore de logement à "
            "vous proposer. Si un logement correspond, je reviens vers vous ici. "
            "Vous pouvez aussi changer un critère : quartier, budget ou type de "
            "logement."
        )
        # Stock copy: the outbound layer may say it in his voice.
        assert staged.answering == "Mais tu me reviens quand ?"

    async def test_taking_the_lodging_already_put_to_its_owner_is_not_a_new_search(
        self, uow: FakeUnitOfWork, session: ConversationSession, message_id: uuid.UUID
    ) -> None:
        """Observed live: "ok je prends le premier", sent while the owner was
        being asked, re-recorded the demand and the same lodging came back."""
        session.start_flow(
            FlowName.PROPERTY_SEARCH, now=NOW, first_step=FlowStep.AWAITING_AVAILABILITY
        )
        session.context.update(
            {
                "property_type": "APARTMENT",
                "location": {"city": "Douala", "neighbourhood": "Bali"},
                "rent": 200_000,
                "advance_months": 3,
            }
        )
        demands = StubDemands()
        use_case, _, _, _ = build(
            uow,
            session,
            message_id,
            text="ok je prends le premier",
            model=StubModel(
                result=_model_result(
                    intent="PROPERTY_SEARCH",
                    entities={"property_type": "APARTMENT", "property_choice": 1},
                )
            ),
            demands=demands,
        )

        await use_case.execute(
            AnalysisCommand(message_id=message_id, session_id=session.id), now=NOW
        )

        assert demands.recorded == []
        assert session.step is FlowStep.AWAITING_AVAILABILITY
        assert "bailleur" in (uow.events.staged[0].text or "")

    async def test_a_follow_up_the_rules_cannot_read_still_gets_the_criteria(
        self, uow: FakeUnitOfWork, session: ConversationSession, message_id: uuid.UUID
    ) -> None:
        """With no model to read it, "tu me reviens quand ?" was answered with a
        request to rephrase; while the demand waits there is nothing to misfile."""
        session.start_flow(
            FlowName.PROPERTY_SEARCH, now=NOW, first_step=FlowStep.AWAITING_MATCH
        )
        session.context.update(
            {
                "property_type": "STUDIO",
                "location": {"city": "Yaoundé", "neighbourhood": "Bastos"},
                "rent": 80_000,
            }
        )
        use_case, _, _, _ = build(
            uow, session, message_id, text="Mais tu me reviens quand ?"
        )

        report = await use_case.execute(
            AnalysisCommand(message_id=message_id, session_id=session.id), now=NOW
        )

        assert report.accepted is False
        assert uow.events.staged[0].text.startswith(
            "Votre recherche est bien enregistrée : Studio - Bastos, Yaoundé - "
            "80 000 FCFA/mois maximum."
        )

    async def test_an_added_place_joins_the_ones_already_accepted(
        self, uow: FakeUnitOfWork, session: ConversationSession, message_id: uuid.UUID
    ) -> None:
        """Read by the rules alone, "Logbessou aussi" replaced Bonamoussadi and
        Makepe instead of joining them."""
        session.start_flow(
            FlowName.PROPERTY_SEARCH, now=NOW, first_step=FlowStep.AWAITING_MATCH
        )
        session.context.update(
            {
                "property_type": "STUDIO",
                "location": {
                    "city": "Douala",
                    "neighbourhood": "Bonamoussadi",
                    "neighbourhoods": ["Bonamoussadi", "Makepe"],
                },
                "rent": 60_000,
                "advance_months": 3,
            }
        )
        demands = StubDemands()
        use_case, _, _, _ = build(
            uow,
            session,
            message_id,
            text="Ah attends, si c'est à Logbessou aussi ça va",
            demands=demands,
        )

        await use_case.execute(
            AnalysisCommand(message_id=message_id, session_id=session.id), now=NOW
        )

        assert session.context["location"]["neighbourhoods"] == [
            "Bonamoussadi",
            "Makepe",
            "Logbessou",
        ]
        assert demands.recorded[0][1].neighbourhoods == (
            "Bonamoussadi",
            "Makepe",
            "Logbessou",
        )

    async def test_a_refused_turn_asks_the_open_question_again(
        self, uow: FakeUnitOfWork, session: ConversationSession, message_id: uuid.UUID
    ) -> None:
        """Observed live: the turn was refused, the quantity stayed open, and
        the model's own sentence had already moved on to the rent."""
        session.start_flow(
            FlowName.PROPERTY_CREATION, now=NOW, first_step=FlowStep.COLLECT_QUANTITY
        )
        session.context.update({"property_type": "STUDIO", "next_question": "ask.quantity"})
        responder = StubResponseModel(text="Je n'ai pas bien compris : combien de logements ?")
        use_case, _, _, _ = build(
            uow,
            session,
            message_id,
            text=AMBIGUOUS_TEXT,
            model=StubModel(
                result=_model_result(
                    intent="COLLECT_QUANTITY",
                    confidence=0.0,
                    entities={},
                    reply="D'accord. Quel loyer demandez-vous ?",
                )
            ),
            response_model=responder,
        )

        report = await use_case.execute(
            AnalysisCommand(message_id=message_id, session_id=session.id), now=NOW
        )

        assert report.accepted is False
        assert responder.calls == 1
        assert json.loads(responder.prompts[0].user)["ask_about"] is not None
        assert "loyer" not in uow.events.staged[0].text

    async def test_an_inline_reply_written_for_the_landlord_form_is_rewritten(
        self, uow: FakeUnitOfWork, session: ConversationSession, message_id: uuid.UUID
    ) -> None:
        """The rules missed a tenant's opening, so the model was shown the
        landlord's fields and asked him how many lodgings his listing offers."""
        responder = StubResponseModel(text="Which neighbourhood are you looking in?")
        use_case, _, _, _ = build(
            uow,
            session,
            message_id,
            text=AMBIGUOUS_TEXT,
            model=StubModel(
                result=_model_result(
                    language="en",
                    intent="PROPERTY_SEARCH",
                    entities={"property_type": "STUDIO", "standing": "MODERN"},
                    reply="How many identical lodgings are on offer in this listing?",
                )
            ),
            response_model=responder,
        )

        await use_case.execute(
            AnalysisCommand(message_id=message_id, session_id=session.id), now=NOW
        )

        assert responder.calls == 1
        assert uow.events.staged[0].text == "Which neighbourhood are you looking in?"


class TestConversationLanguage:
    """The message decides the language; the session only remembers it.

    The model reads the message, so the pipeline does not impose French. It keeps
    the model's reading to answer a later message whose language cannot be told
    on its own (a bare "150000").
    """

    async def test_the_model_reported_language_is_remembered(
        self,
        uow: FakeUnitOfWork,
        session: ConversationSession,
        message_id: uuid.UUID,
    ) -> None:
        model = StubModel(result=_model_result(language="en"))
        use_case, _, _, sessions = build(
            uow,
            session,
            message_id,
            text="Hello, I am looking to rent a flat in Bastos",
            model=model,
        )

        await use_case.execute(
            AnalysisCommand(message_id=message_id, session_id=session.id), now=NOW
        )

        assert session.language == "en"
        assert sessions.store[session.id].language == "en"

    async def test_the_prompt_does_not_impose_a_language(
        self,
        uow: FakeUnitOfWork,
        session: ConversationSession,
        message_id: uuid.UUID,
    ) -> None:
        """A forced "language" is what made the assistant answer French to an
        English message; the model is handed the message, not a verdict."""
        model = StubModel(result=_model_result(language="en"))
        use_case, _, _, _ = build(
            uow,
            session,
            message_id,
            text="Hello, I am looking to rent a flat in Bastos",
            model=model,
        )

        await use_case.execute(
            AnalysisCommand(message_id=message_id, session_id=session.id), now=NOW
        )

        payload = json.loads(model.prompts[0].user)
        assert "language" not in payload
        assert payload["default_language"] == "fr"
        assert payload["message"] == "Hello, I am looking to rent a flat in Bastos"

    async def test_an_unusable_report_leaves_the_session_language(
        self,
        uow: FakeUnitOfWork,
        session: ConversationSession,
        message_id: uuid.UUID,
    ) -> None:
        session.language = "en"
        use_case, _, _, _ = build(
            uow, session, message_id, model=StubModel(result=_model_result(language="es"))
        )

        await use_case.execute(
            AnalysisCommand(message_id=message_id, session_id=session.id), now=NOW
        )

        assert session.language == "en"


class FakeAvailabilityAnswers:
    """The catalogue's half of the availability check, as the pipeline sees it."""

    def __init__(
        self,
        *,
        result: Any | None = None,
        asked: bool = True,
    ) -> None:
        self._result = result
        self._asked = asked
        self.calls: list[tuple[uuid.UUID, str]] = []
        self.offer_replies: list[Any] = []

    async def record_answer(
        self,
        *,
        landlord_id: uuid.UUID,
        answer: str,
        now: datetime,
        offer_reply: Any | None = None,
    ) -> Any | None:
        self.calls.append((landlord_id, answer))
        self.offer_replies.append(offer_reply)
        if not self._asked:
            return None
        return self._result


class TestTheLandlordsAvailabilityAnswerIsActedOn:
    """The intents were recognised and then thrown away.

    ``AVAILABILITY_RENTED`` has been in the allowed-intent list from the start,
    and nothing consumed it: a landlord could write "c'est déjà loué" and his
    lodging stayed in every tenant's search results. Nothing else in the system
    can take a listing out — Kora never infers availability — so if this turn
    does nothing, the listing is never withdrawn at all.
    """

    async def test_an_answer_reaches_the_catalogue(
        self,
        uow: FakeUnitOfWork,
        session: ConversationSession,
        message_id: uuid.UUID,
    ) -> None:
        answers = FakeAvailabilityAnswers(
            result=AvailabilityAnswerResult(
                confirms_availability=False, lodgings_left=0, delisted=True
            )
        )
        use_case, _, _, _ = build(
            uow,
            session,
            message_id,
            text="c'est déjà loué",
            availability=answers,
        )

        await use_case.execute(
            AnalysisCommand(message_id=message_id, session_id=session.id), now=NOW
        )

        assert answers.calls == [(session.user_id, "AVAILABILITY_RENTED")]

    async def test_the_open_question_decides_over_the_model(
        self,
        uow: FakeUnitOfWork,
        session: ConversationSession,
        message_id: uuid.UUID,
    ) -> None:
        """Observed live: mid-way through his photos, "Oui, il est toujours
        disponible" came back AFFIRMATIVE and was read as small talk."""
        session.start_flow(
            FlowName.PROPERTY_CREATION, now=NOW, first_step=FlowStep.COLLECT_MEDIA
        )
        session.context["next_question"] = "ask.availability_check"
        answers = FakeAvailabilityAnswers(
            result=AvailabilityAnswerResult(
                confirms_availability=True, lodgings_left=1, delisted=False
            )
        )
        use_case, _, _, _ = build(
            uow,
            session,
            message_id,
            text="Oui, il est toujours disponible pour le locataire",
            model=StubModel(result=_model_result(intent="AFFIRMATIVE", entities={})),
            availability=answers,
        )

        await use_case.execute(
            AnalysisCommand(message_id=message_id, session_id=session.id), now=NOW
        )

        assert answers.calls == [(session.user_id, "AVAILABILITY_STILL_AVAILABLE")]
        # Answered, it is closed: he is back on the question of his own form.
        assert session.context["next_question"] == "ask.media"

    async def test_a_bare_oui_read_by_the_fast_lane_still_answers(
        self,
        uow: FakeUnitOfWork,
        session: ConversationSession,
        message_id: uuid.UUID,
    ) -> None:
        """Observed live: the landlord's bare "oui" was read by the regex lane,
        which skipped the open-question reading, so his answer was taken for
        small talk and the tenant was never released until he rephrased as
        "oui c'est disponible"."""
        session.start_flow(
            FlowName.PROPERTY_CREATION, now=NOW, first_step=FlowStep.COLLECT_MEDIA
        )
        session.context["next_question"] = "ask.availability_check"
        answers = FakeAvailabilityAnswers(
            result=AvailabilityAnswerResult(
                confirms_availability=True, lodgings_left=1, delisted=False
            )
        )
        model = StubModel(result=_model_result(intent="AFFIRMATIVE", entities={}))
        use_case, turns, _, _ = build(
            uow, session, message_id, text="Oui", model=model, availability=answers
        )

        await use_case.execute(
            AnalysisCommand(message_id=message_id, session_id=session.id), now=NOW
        )

        assert model.calls == 0
        assert turns.turns[0].used_llm is False
        assert answers.calls == [(session.user_id, "AVAILABILITY_STILL_AVAILABLE")]
        # Answered, it is closed: he is back on the question of his own form.
        assert session.context["next_question"] == "ask.media"

    async def test_the_landlord_is_told_the_listing_left_the_catalogue(
        self,
        uow: FakeUnitOfWork,
        session: ConversationSession,
        message_id: uuid.UUID,
    ) -> None:
        """Never a bare "c'est noté": he needs to know no tenant will be sent."""
        answers = FakeAvailabilityAnswers(
            result=AvailabilityAnswerResult(
                confirms_availability=False, lodgings_left=0, delisted=True
            )
        )
        use_case, _, _, _ = build(
            uow, session, message_id, text="tout a été pris", availability=answers
        )

        await use_case.execute(
            AnalysisCommand(message_id=message_id, session_id=session.id), now=NOW
        )

        assert "ne sera plus proposé" in uow.events.staged[-1].text

    async def test_a_landlord_with_lodgings_left_is_told_so(
        self,
        uow: FakeUnitOfWork,
        session: ConversationSession,
        message_id: uuid.UUID,
    ) -> None:
        answers = FakeAvailabilityAnswers(
            result=AvailabilityAnswerResult(
                confirms_availability=True, lodgings_left=2, delisted=False
            )
        )
        use_case, _, _, _ = build(
            uow, session, message_id, text="un studio a été pris", availability=answers
        )

        await use_case.execute(
            AnalysisCommand(message_id=message_id, session_id=session.id), now=NOW
        )

        assert "2" in uow.events.staged[-1].text

    async def test_an_answer_to_no_question_leaves_the_reply_alone(
        self,
        uow: FakeUnitOfWork,
        session: ConversationSession,
        message_id: uuid.UUID,
    ) -> None:
        """He was never asked, so there is nothing to acknowledge and the turn
        is answered the way any other would be."""
        answers = FakeAvailabilityAnswers(asked=False)
        use_case, _, _, _ = build(
            uow, session, message_id, text="c'est déjà loué", availability=answers
        )

        await use_case.execute(
            AnalysisCommand(message_id=message_id, session_id=session.id), now=NOW
        )

        assert answers.calls
        # No acknowledgement of its own: the turn is answered the way any other
        # would be, which with no model configured is the fallback copy.
        staged = uow.events.staged[-1]
        assert staged.text is None
        assert staged.reply_kind == "ACK"


class FakeAvailabilityRequests:
    """Starts the landlord's question, and remembers what it was asked about."""

    def __init__(self, *, accepted: bool = True) -> None:
        self._accepted = accepted
        self.calls: list[tuple[uuid.UUID, uuid.UUID | None]] = []

    async def request(
        self, *, property_id: uuid.UUID, tenant_id: uuid.UUID | None, now: datetime
    ) -> bool:
        self.calls.append((property_id, tenant_id))
        return self._accepted


class TestTheTenantsChoiceReachesTheLandlord:
    """Where the funnel used to end.

    The list was printed, the number was asked for, the answer was understood —
    and nothing consumed it. The tenant reached the last step of the product
    and the conversation stopped.
    """

    def waiting_for_a_choice(
        self, session: ConversationSession, *, shown: list[str]
    ) -> None:
        session.start_flow(
            FlowName.PROPERTY_SEARCH,
            now=NOW,
            first_step=FlowStep.COLLECT_PROPERTY_CHOICE,
        )
        session.context["_shown_listings"] = shown
        session.context["next_question"] = "ask.search_choice"

    async def test_the_pick_does_not_reopen_his_search(
        self,
        uow: FakeUnitOfWork,
        session: ConversationSession,
        message_id: uuid.UUID,
    ) -> None:
        """Observed live: with his criteria all stored, "1" also completed the
        search form, re-recorded the demand the list had just answered, and he
        was sent the same list again under "votre demande est enregistrée"."""
        self.waiting_for_a_choice(session, shown=[str(uuid.uuid4())])
        session.context.update(
            {
                "property_type": "STUDIO",
                "location": {"city": "Yaoundé", "neighbourhood": "Jouvence"},
                "rent": 45_000,
            }
        )
        demands = StubDemands()
        use_case, _, _, _ = build(
            uow,
            session,
            message_id,
            text="1",
            availability_requests=FakeAvailabilityRequests(),
            demands=demands,
        )

        await use_case.execute(
            AnalysisCommand(message_id=message_id, session_id=session.id), now=NOW
        )

        assert demands.recorded == []
        assert session.step is FlowStep.AWAITING_AVAILABILITY
        assert "enregistrée" not in (uow.events.staged[-1].text or "")

    async def test_the_number_becomes_a_question_to_the_right_landlord(
        self,
        uow: FakeUnitOfWork,
        session: ConversationSession,
        message_id: uuid.UUID,
    ) -> None:
        first, second = str(uuid.uuid4()), str(uuid.uuid4())
        self.waiting_for_a_choice(session, shown=[first, second])
        requests = FakeAvailabilityRequests()
        use_case, _, _, _ = build(
            uow, session, message_id, text="2", availability_requests=requests
        )

        await use_case.execute(
            AnalysisCommand(message_id=message_id, session_id=session.id), now=NOW
        )

        assert requests.calls == [(uuid.UUID(second), session.user_id)]

    async def test_the_tenant_is_told_the_landlord_is_being_asked(
        self,
        uow: FakeUnitOfWork,
        session: ConversationSession,
        message_id: uuid.UUID,
    ) -> None:
        """Not "réservé", and no promise of a visit: only what is happening."""
        self.waiting_for_a_choice(session, shown=[str(uuid.uuid4())])
        use_case, _, _, _ = build(
            uow,
            session,
            message_id,
            text="1",
            availability_requests=FakeAvailabilityRequests(),
        )

        await use_case.execute(
            AnalysisCommand(message_id=message_id, session_id=session.id), now=NOW
        )

        text = uow.events.staged[-1].text or ""
        assert "demande au bailleur" in text
        assert "réserv" not in text
        assert session.step is FlowStep.AWAITING_AVAILABILITY

    async def test_a_lone_result_is_taken_on_a_bare_acceptance(
        self,
        uow: FakeUnitOfWork,
        session: ConversationSession,
        message_id: uuid.UUID,
    ) -> None:
        """Observed live: one lodging was shown and the tenant answered "je
        prends"; he was asked for a number he had nothing to choose between."""
        listing = str(uuid.uuid4())
        self.waiting_for_a_choice(session, shown=[listing])
        requests = FakeAvailabilityRequests()
        use_case, _, _, _ = build(
            uow,
            session,
            message_id,
            text="je prends",
            model=StubModel(result=_model_result(intent="AFFIRMATIVE", entities={})),
            availability_requests=requests,
        )

        await use_case.execute(
            AnalysisCommand(message_id=message_id, session_id=session.id), now=NOW
        )

        assert requests.calls == [(uuid.UUID(listing), session.user_id)]
        assert session.step is FlowStep.AWAITING_AVAILABILITY

    async def test_an_acceptance_against_a_list_is_still_asked_again(
        self,
        uow: FakeUnitOfWork,
        session: ConversationSession,
        message_id: uuid.UUID,
    ) -> None:
        """With several lodgings shown, "je prends" names none of them: resolving
        it to the first would charge him for one he did not pick."""
        self.waiting_for_a_choice(session, shown=[str(uuid.uuid4()), str(uuid.uuid4())])
        requests = FakeAvailabilityRequests()
        use_case, _, _, _ = build(
            uow,
            session,
            message_id,
            text="je prends",
            model=StubModel(result=_model_result(intent="AFFIRMATIVE", entities={})),
            availability_requests=requests,
        )

        await use_case.execute(
            AnalysisCommand(message_id=message_id, session_id=session.id), now=NOW
        )

        assert requests.calls == []
        assert session.step is FlowStep.COLLECT_PROPERTY_CHOICE

    async def test_a_number_naming_nothing_is_asked_again(
        self,
        uow: FakeUnitOfWork,
        session: ConversationSession,
        message_id: uuid.UUID,
    ) -> None:
        """Past the end of the list. Resolving it to the nearest listing would
        charge him for a lodging he never saw."""
        self.waiting_for_a_choice(session, shown=[str(uuid.uuid4())])
        requests = FakeAvailabilityRequests()
        use_case, _, _, _ = build(
            uow, session, message_id, text="4", availability_requests=requests
        )

        await use_case.execute(
            AnalysisCommand(message_id=message_id, session_id=session.id), now=NOW
        )

        assert requests.calls == []
        assert session.step is FlowStep.COLLECT_PROPERTY_CHOICE

    async def test_a_choice_against_a_list_he_never_saw_is_asked_again(
        self,
        uow: FakeUnitOfWork,
        session: ConversationSession,
        message_id: uuid.UUID,
    ) -> None:
        self.waiting_for_a_choice(session, shown=[])
        requests = FakeAvailabilityRequests()
        use_case, _, _, _ = build(
            uow, session, message_id, text="1", availability_requests=requests
        )

        await use_case.execute(
            AnalysisCommand(message_id=message_id, session_id=session.id), now=NOW
        )

        assert requests.calls == []
        assert "ne correspond à aucun logement" in (uow.events.staged[-1].text or "")

    async def test_a_listing_gone_since_the_search_is_asked_again(
        self,
        uow: FakeUnitOfWork,
        session: ConversationSession,
        message_id: uuid.UUID,
    ) -> None:
        """Deleted between the results and the choice: he is told his number
        names nothing, which is true, rather than shown a failure."""
        self.waiting_for_a_choice(session, shown=[str(uuid.uuid4())])
        use_case, _, _, _ = build(
            uow,
            session,
            message_id,
            text="1",
            availability_requests=FakeAvailabilityRequests(accepted=False),
        )

        await use_case.execute(
            AnalysisCommand(message_id=message_id, session_id=session.id), now=NOW
        )

        assert session.step is FlowStep.COLLECT_PROPERTY_CHOICE
        assert "ne correspond à aucun logement" in (uow.events.staged[-1].text or "")

    async def test_the_shown_listings_never_reach_a_prompt(
        self,
        uow: FakeUnitOfWork,
        session: ConversationSession,
        message_id: uuid.UUID,
    ) -> None:
        """Bookkeeping, not a fact the tenant stated: it would cost tokens and
        read back as something he said."""
        listing_id = str(uuid.uuid4())
        self.waiting_for_a_choice(session, shown=[listing_id])
        model = StubModel(result=_model_result())
        use_case, _, _, _ = build(
            uow,
            session,
            message_id,
            text="une phrase indeterminate",
            model=model,
            availability_requests=FakeAvailabilityRequests(),
        )

        await use_case.execute(
            AnalysisCommand(message_id=message_id, session_id=session.id), now=NOW
        )

        assert listing_id not in model.prompts[0].user


class StubFollowUp:
    def __init__(self) -> None:
        self.calls: list[tuple[str, uuid.UUID]] = []

    async def renew(self, *, tenant_id: uuid.UUID, now: datetime) -> bool:
        self.calls.append(("renew", tenant_id))
        return True

    async def stop(self, *, tenant_id: uuid.UUID, now: datetime) -> bool:
        self.calls.append(("stop", tenant_id))
        return True


class TestStillSearching:
    """A month on: "cherchez-vous toujours ?" is answered by a rule."""

    def _asked(self, session: ConversationSession) -> None:
        session.start_flow(
            FlowName.PROPERTY_SEARCH, now=NOW, first_step=FlowStep.COLLECT_PROPERTY_CHOICE
        )
        session.context["next_question"] = "ask.still_searching"

    @pytest.mark.parametrize(
        ("text", "move", "reply"),
        [
            ("oui je cherche toujours", "renew", "je continue à chercher"),
            ("non merci j'ai trouvé", "stop", "j'arrête la recherche"),
        ],
    )
    async def test_yes_or_no_settles_the_watch(
        self,
        uow: FakeUnitOfWork,
        session: ConversationSession,
        message_id: uuid.UUID,
        text: str,
        move: str,
        reply: str,
    ) -> None:
        self._asked(session)
        follow_up = StubFollowUp()
        use_case, _, _, _ = build(uow, session, message_id, text=text, follow_up=follow_up)

        report = await use_case.execute(
            AnalysisCommand(message_id=message_id, session_id=session.id), now=NOW
        )

        assert report.used_llm is False
        assert follow_up.calls == [(move, session.user_id)]
        assert "next_question" not in session.context
        assert reply in uow.events.staged[0].text

    async def test_a_changed_search_goes_on_to_the_pipeline(
        self, uow: FakeUnitOfWork, session: ConversationSession, message_id: uuid.UUID
    ) -> None:
        self._asked(session)
        follow_up = StubFollowUp()
        use_case, _, _, _ = build(
            uow, session, message_id, text="oui mais à Bastos maintenant", follow_up=follow_up
        )

        await use_case.execute(
            AnalysisCommand(message_id=message_id, session_id=session.id), now=NOW
        )

        assert follow_up.calls == []


class TestASecondListing:
    async def test_another_lodging_after_a_publication_starts_a_fresh_form(
        self, uow: FakeUnitOfWork, session: ConversationSession, message_id: uuid.UUID
    ) -> None:
        """Observed live: "j'ai encore un autre studio" went straight to the
        summary of the listing already published, and its photos were
        attached to that one, which was rented."""
        published = uuid.uuid4()
        session.start_flow(FlowName.PROPERTY_CREATION, now=NOW, first_step=FlowStep.PUBLISHED)
        session.context.update(
            {
                "property_type": "STUDIO",
                "location": {"city": "Yaoundé", "neighbourhood": "Ékounou"},
                "rent": 65_000,
            }
        )
        session.attach_property(published, now=NOW)
        use_case, _, _, _ = build(
            uow,
            session,
            message_id,
            text="j'ai encore un autre studio à louer",
            model=StubModel(
                result=_model_result(
                    intent="CREATE_PROPERTY", entities={"property_type": "STUDIO"}
                )
            ),
        )

        await use_case.execute(
            AnalysisCommand(message_id=message_id, session_id=session.id), now=NOW
        )

        assert session.assigned_property_id is None
        assert "rent" not in session.context
        assert session.step is not FlowStep.CONFIRM_PROPERTY


class TestTheLandlordAnswersAnOffer:
    """§8: his yes, no or price to the tenant's terms, read by a rule."""

    @pytest.mark.parametrize(
        ("text", "outcome", "rent"),
        [("oui ça me va", "ACCEPTED", None), ("62 mil", "COUNTERED", 62_000)],
    )
    async def test_his_reply_reaches_the_catalogue(
        self,
        uow: FakeUnitOfWork,
        session: ConversationSession,
        message_id: uuid.UUID,
        text: str,
        outcome: str,
        rent: int | None,
    ) -> None:
        session.start_flow(FlowName.PROPERTY_CREATION, now=NOW, first_step=FlowStep.PUBLISHED)
        session.context["next_question"] = "ask.availability_offer"
        answers = FakeAvailabilityAnswers(
            result=AvailabilityAnswerResult(
                confirms_availability=True, lodgings_left=1, delisted=False
            )
        )
        use_case, _, _, _ = build(uow, session, message_id, text=text, availability=answers)

        await use_case.execute(
            AnalysisCommand(message_id=message_id, session_id=session.id), now=NOW
        )

        assert answers.calls == [(session.user_id, "AVAILABILITY_STILL_AVAILABLE")]
        assert answers.offer_replies[0]["outcome"] == outcome
        assert answers.offer_replies[0]["rent_xaf"] == rent
        assert "next_question" not in session.context

    async def test_a_lodging_gone_is_read_as_before(
        self, uow: FakeUnitOfWork, session: ConversationSession, message_id: uuid.UUID
    ) -> None:
        session.start_flow(FlowName.PROPERTY_CREATION, now=NOW, first_step=FlowStep.PUBLISHED)
        session.context["next_question"] = "ask.availability_offer"
        answers = FakeAvailabilityAnswers(
            result=AvailabilityAnswerResult(
                confirms_availability=False, lodgings_left=0, delisted=True
            )
        )
        use_case, _, _, _ = build(
            uow, session, message_id, text="c'est déjà loué", availability=answers
        )

        await use_case.execute(
            AnalysisCommand(message_id=message_id, session_id=session.id), now=NOW
        )

        assert answers.calls == [(session.user_id, "AVAILABILITY_RENTED")]
        assert answers.offer_replies == [None]
