"""The analysis pipeline: what it costs, what it trusts, what it refuses.

Every guarantee the pipeline claims is asserted here against fakes, because the
point of these tests is the *decisions*: whether a message cost a model call,
whether a transaction was open while it did, and whether an untrusted value ever
reached the session.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from datetime import UTC, datetime
from decimal import Decimal
from typing import Any

import pytest

from loka.bounded_contexts.ai.application.ports import (
    BudgetPolicy,
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
from loka.bounded_contexts.ai.domain.services.ai_provider import IntentResult, LlmUsage, Prompt
from loka.bounded_contexts.identity.domain.value_objects.phone_number import PhoneNumber
from loka.bounded_contexts.messaging.domain.entities.conversation_session import (
    ConversationSession,
    FlowStep,
)
from loka.bounded_contexts.messaging.domain.repositories.inbound_message_repository import (
    StoredInboundMessage,
)
from loka.shared.application.unit_of_work import UnitOfWork
from loka.shared.domain.errors import ConcurrencyConflict, InvalidStateTransition

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
class StubModel:
    """Records whether it was called, and how often."""

    result: IntentResult
    calls: int = 0

    @property
    def model(self) -> str:
        return TEST_MODEL

    async def understand_intent(self, prompt: Prompt) -> IntentResult:
        self.calls += 1
        return self.result


class FakeUnitOfWork(UnitOfWork):
    def __init__(self) -> None:
        self.commits = 0
        self.open_commits: list[int] = []
        self.commits_at_model_call: list[int] = []

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
    def events(self) -> Any:
        raise NotImplementedError

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
) -> IntentResult:
    return IntentResult(
        intent=intent,
        confidence=confidence,
        entities=entities if entities is not None else {"property_type": "APARTMENT"},
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
    budget: BudgetPolicy | None = None,
    ledger: FakeLedger | None = None,
    sessions: FakeSessionRepository | None = None,
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
        budget=budget,
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
                    entities={"property_type": "APARTMENT", "city": "Douala"}
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
                        "city": "Douala",
                        "price": 250_000,
                        "bedrooms": 2,
                    }
                )
            ),
        )

        report = await use_case.execute(
            AnalysisCommand(message_id=message_id, session_id=session.id), now=NOW
        )

        assert session.step is FlowStep.CONFIRM_PROPERTY
        assert report.next_step == "CONFIRM_PROPERTY"

    async def test_the_revision_is_bumped_when_state_moves(
        self, uow: FakeUnitOfWork, session: ConversationSession, message_id: uuid.UUID
    ) -> None:
        before = session.revision
        use_case, _, _, _ = build(uow, session, message_id, text="250 000 fcfa")

        await use_case.execute(
            AnalysisCommand(message_id=message_id, session_id=session.id), now=NOW
        )

        assert session.revision > before
