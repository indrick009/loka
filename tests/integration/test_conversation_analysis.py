"""The analysis pipeline against real PostgreSQL.

The unit tests prove the decisions; these prove the durability. What only a real
database can show is that the turn, the usage and the session move together, that
the cost the ledger sums is the cost the caller was told, and that the optimistic
lock actually refuses a second writer.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta
from decimal import Decimal

import pytest
from sqlalchemy import func, select

from loka.bounded_contexts.ai.application.ports import (
    BudgetPolicy,
    ConversationTurn,
    IntentModel,
)
from loka.bounded_contexts.ai.application.use_cases.analyse_conversation_message import (
    USE_CASE,
    AnalyseConversationMessageUseCase,
    AnalysisCommand,
)
from loka.bounded_contexts.ai.domain.services.ai_provider import IntentResult, LlmUsage, Prompt
from loka.bounded_contexts.ai.infrastructure.persistence.models import AiUsageLedgerRow
from loka.bounded_contexts.ai.infrastructure.persistence.repositories import (
    SqlAlchemyAiUsageLedger,
    SqlAlchemyConversationTurnRepository,
)
from loka.bounded_contexts.identity.infrastructure.persistence.models import UserRow
from loka.bounded_contexts.messaging.application.use_cases.receive_inbound_message import (
    ReceiveInboundMessageCommand,
)
from loka.bounded_contexts.messaging.domain.entities.conversation_session import FlowStep
from loka.bounded_contexts.messaging.infrastructure.composition import (
    receive_inbound_message_use_case,
)
from loka.bounded_contexts.messaging.infrastructure.persistence.models import (
    ConversationSessionRow,
    ConversationTurnRow,
    InboundMessageRow,
)
from loka.shared.domain.errors import ConcurrencyConflict
from loka.shared.infrastructure.composition import unit_of_work
from loka.shared.infrastructure.config.settings import Settings
from loka.shared.infrastructure.crypto.blind_index import blind_index, derive_blind_index_key
from loka.shared.infrastructure.db.engine import Database
from loka.shared.infrastructure.db.outbox import OutboxRecord

pytestmark = pytest.mark.integration

NOW = datetime(2026, 3, 1, 9, 0, tzinfo=UTC)
PHONE = "+237699123456"
TEST_MODEL = "google/gemini-2.5-flash"


class StubModel:
    """A model whose answer and cost are fully under the test's control."""

    def __init__(self, result: IntentResult) -> None:
        self._result = result
        self.calls = 0

    @property
    def model(self) -> str:
        return TEST_MODEL

    async def understand_intent(self, prompt: Prompt) -> IntentResult:
        self.calls += 1
        return self._result


def result(
    *,
    intent: str = "CREATE_PROPERTY",
    confidence: float = 0.9,
    entities: dict[str, object] | None = None,
    cost: float = 0.0004,
) -> IntentResult:
    return IntentResult(
        intent=intent,
        confidence=confidence,
        entities=entities if entities is not None else {"property_type": "APARTMENT"},
        usage=LlmUsage(
            input_tokens=120,
            output_tokens=60,
            latency_ms=310,
            estimated_cost_usd=cost,
            model=TEST_MODEL,
        ),
    )


def _key(settings: Settings) -> bytes:
    return derive_blind_index_key(settings.encryption_key.get_secret_value())


async def _seed(
    database: Database,
    settings: Settings,
    *,
    text: str = "Je veux publier un appartement a 250 000 fcfa",
) -> tuple[uuid.UUID, uuid.UUID]:
    """Ingest one message the way the WhatsApp consumer does.

    The account is created once per test and reused afterwards: several messages
    from one landlord is the normal shape, and a second insert would only
    violate the unique phone constraint.
    """
    now = datetime.now(UTC)
    async with database.session() as session:
        existing = (
            await session.execute(select(UserRow.id).where(UserRow.phone_e164 == PHONE))
        ).scalar_one_or_none()
    if existing is None:
        user_id = uuid.uuid4()
        async with database.session() as session:
            session.add(
                UserRow(
                    id=user_id,
                    phone_e164=PHONE,
                    phone_e164_hash=blind_index(PHONE, key=_key(settings)),
                    roles=["LANDLORD"],
                    status="ACTIVE",
                    locale="fr",
                    created_at=now,
                    updated_at=now,
                )
            )
            await session.commit()
    else:
        user_id = existing

    async with unit_of_work(database) as uow:
        receipt = await receive_inbound_message_use_case(
            uow, index_key=_key(settings)
        ).execute(
            ReceiveInboundMessageCommand(
                provider="baileys",
                external_message_id=f"BAIL_{uuid.uuid4().hex[:8]}",
                conversation_ref=f"{user_id}@s.whatsapp.net",
                sender_phone=PHONE,
                message_kind="TEXT",
                provider_timestamp=NOW,
                text=text,
            ),
            now=NOW,
        )
    assert receipt.session_id is not None
    return receipt.message_id, receipt.session_id


async def _analyse(
    database: Database,
    message_id: uuid.UUID,
    session_id: uuid.UUID,
    *,
    model: IntentModel | None = None,
    budget: BudgetPolicy | None = None,
) -> tuple[object, object]:
    async with unit_of_work(database) as uow:
        turns = SqlAlchemyConversationTurnRepository(uow.session)
        usage = SqlAlchemyAiUsageLedger(uow.session)
        use_case = AnalyseConversationMessageUseCase(
            uow,
            inbound=uow.repository("inbound_messages"),
            sessions=uow.repository("conversation_sessions"),
            turns=turns,
            usage=usage,
            model=model,
            budget=budget,
        )
        report = await use_case.execute(
            AnalysisCommand(message_id=message_id, session_id=session_id), now=NOW
        )
    return report, use_case


class TestPersistence:
    async def test_a_deterministic_answer_is_recorded_as_a_turn(
        self, database: Database, integration_settings: Settings
    ) -> None:
        """The text resolves by rule, so nothing is asked of a model."""
        message_id, session_id = await _seed(database, integration_settings)

        report, _ = await _analyse(database, message_id, session_id)

        assert report.used_llm is False
        assert report.intent == "CREATE_PROPERTY"
        async with database.session() as session:
            turns = list(
                (
                    await session.execute(
                        select(ConversationTurnRow).where(
                            ConversationTurnRow.session_id == session_id
                        )
                    )
                )
                .scalars()
            )
        assert len(turns) == 1
        assert turns[0].used_llm is False
        assert turns[0].message_id == message_id
        assert turns[0].created_at is not None

    async def test_the_session_state_survives_the_transaction(
        self, database: Database, integration_settings: Settings
    ) -> None:
        message_id, session_id = await _seed(database, integration_settings)

        await _analyse(database, message_id, session_id)

        async with database.session() as session:
            row = await session.get(ConversationSessionRow, session_id)
        assert row is not None
        assert row.revision > 0
        assert row.context_json["rent"] == 250_000
        assert row.context_json["next_question"] == "ask.location"

    async def test_a_model_answer_is_costed_in_the_ledger(
        self, database: Database, integration_settings: Settings
    ) -> None:
        message_id, session_id = await _seed(
            database, integration_settings, text="Je vous explique mon cas"
        )
        model = StubModel(result(cost=0.0004))

        report, _ = await _analyse(database, message_id, session_id, model=model)

        assert model.calls == 1
        assert report.used_llm is True
        async with database.session() as session:
            entry = (
                await session.execute(
                    select(AiUsageLedgerRow).where(
                        AiUsageLedgerRow.use_case == USE_CASE
                    )
                )
            ).scalars().one()
        assert entry.cost_usd == Decimal("0.0004")
        assert entry.input_tokens == 120
        assert entry.output_tokens == 60
        assert entry.latency_ms == 310
        assert entry.model == TEST_MODEL
        assert entry.conversation_id == session_id
        assert entry.occurred_on == NOW.date()

    async def test_a_refused_answer_leaves_the_session_alone(
        self, database: Database, integration_settings: Settings
    ) -> None:
        message_id, session_id = await _seed(
            database, integration_settings, text="Je vous explique mon cas"
        )
        report, _ = await _analyse(
            database,
            message_id,
            session_id,
            model=StubModel(result(confidence=0.2, entities={"property_type": "APARTMENT"})),
        )

        assert report.accepted is False
        assert report.reason == "low_confidence"
        async with database.session() as session:
            row = await session.get(ConversationSessionRow, session_id)
            turns = await session.scalar(
                select(func.count())
                .select_from(ConversationTurnRow)
                .where(ConversationTurnRow.session_id == session_id)
            )
        assert row is not None
        assert row.context_json == {}
        assert row.revision == 0
        # The attempt is still measured, it just does not change anything.
        assert turns == 1

    async def test_a_nonsense_price_is_refused_and_reported(
        self, database: Database, integration_settings: Settings
    ) -> None:
        message_id, session_id = await _seed(
            database, integration_settings, text="Je vous explique mon cas"
        )

        report, _ = await _analyse(
            database,
            message_id,
            session_id,
            model=StubModel(result(entities={"property_type": "APARTMENT", "price": 10**14})),
        )

        assert "rent" in report.rejected_facts
        async with database.session() as session:
            row = await session.get(ConversationSessionRow, session_id)
        assert row is not None
        assert "rent" not in row.context_json

    async def test_the_message_text_is_read_back_from_messaging(
        self, database: Database, integration_settings: Settings
    ) -> None:
        """The event carried ids only; the pipeline is what reloads the text."""

        seen: list[str | None] = []

        class PeekingModel:
            @property
            def model(self) -> str:
                return TEST_MODEL

            async def understand_intent(self, prompt: Prompt) -> IntentResult:
                seen.append(prompt.user)
                return result()

        ambiguous_id, ambiguous_session = await _seed(
            database, integration_settings, text="Je vous explique mon cas"
        )
        await _analyse(database, ambiguous_id, ambiguous_session, model=PeekingModel())

        assert seen and "explique mon cas" in seen[0]
        assert PHONE not in seen[0]


class TestBudgetAgainstTheLedger:
    async def test_spend_already_recorded_blocks_the_next_call(
        self, database: Database, integration_settings: Settings
    ) -> None:
        message_id, session_id = await _seed(
            database, integration_settings, text="Je vous explique mon cas"
        )
        first = StubModel(result(cost=0.0004))
        await _analyse(database, message_id, session_id, model=first)

        second_message, second_session = await _seed(
            database, integration_settings, text="Encore une question"
        )
        second = StubModel(result(cost=0.0004))
        # The cap is exactly what the first call already spent: the guard stops
        # *at* the budget, it does not let one more call through.
        report, _ = await _analyse(
            database,
            second_message,
            second_session,
            model=second,
            budget=BudgetPolicy(daily_usd=Decimal("0.0004"), per_user_daily_usd=Decimal("10")),
        )

        assert first.calls == 1
        assert second.calls == 0
        assert report.reason == "daily_budget_exhausted"

    async def test_the_ledger_sum_matches_the_recorded_entries(
        self, database: Database, integration_settings: Settings
    ) -> None:
        message_id, session_id = await _seed(
            database, integration_settings, text="Je vous explique mon cas"
        )
        await _analyse(database, message_id, session_id, model=StubModel(result(cost=0.0004)))

        async with database.session() as session:
            total = await SqlAlchemyAiUsageLedger(session).total_cost_usd(on=NOW.date())

        assert total == Decimal("0.000400")

    async def test_a_deterministic_message_costs_nothing_at_all(
        self, database: Database, integration_settings: Settings
    ) -> None:
        # A bare amount is the fast lane: even with a model configured it must
        # not spend a call, so the ledger stays empty.
        message_id, session_id = await _seed(
            database, integration_settings, text="250 000 fcfa"
        )
        model = StubModel(result())

        await _analyse(database, message_id, session_id, model=model)

        assert model.calls == 0
        async with database.session() as session:
            entries = await session.scalar(
                select(func.count()).select_from(AiUsageLedgerRow)
            )
        assert entries == 0


class TestConcurrency:
    async def test_a_session_moved_while_the_model_thinks_is_not_overwritten(
        self, database: Database, integration_settings: Settings
    ) -> None:
        """The real race, in the real window.

        The use case commits before calling the model so it does not hold a
        connection. That means another worker can advance the same session while
        this one waits on the network. The optimistic revision check is the only
        thing standing between the two writes, so this test moves the session
        from inside the model call and requires the pipeline to refuse rather than
        overwrite.
        """
        message_id, session_id = await _seed(
            database, integration_settings, text="Je vous explique mon cas"
        )

        class RacingModel:
            @property
            def model(self) -> str:
                return TEST_MODEL

            async def understand_intent(self, prompt: Prompt) -> IntentResult:
                async with database.session() as other:
                    row = await other.get(ConversationSessionRow, session_id)
                    assert row is not None
                    row.revision = 42
                    row.step = FlowStep.COLLECT_MEDIA.value
                    await other.commit()
                return result()

        with pytest.raises(ConcurrencyConflict):
            await _analyse(database, message_id, session_id, model=RacingModel())

        async with database.session() as session:
            row = await session.get(ConversationSessionRow, session_id)
        assert row is not None
        # The other writer's step survived: no last-write-wins.
        assert row.step == FlowStep.COLLECT_MEDIA.value
        assert row.revision == 42

    async def test_two_sequential_analyses_of_one_session_both_succeed(
        self, database: Database, integration_settings: Settings
    ) -> None:
        """No false positives: a session nobody else touched must accept both."""
        first_id, session_id = await _seed(
            database, integration_settings, text="appartement"
        )
        second_id, _ = await _seed(database, integration_settings, text="250 000 fcfa")

        await _analyse(database, first_id, session_id)
        await _analyse(database, second_id, session_id)

        async with database.session() as session:
            row = await session.get(ConversationSessionRow, session_id)
        assert row is not None
        assert row.revision > 0
        assert row.context_json["rent"] == 250_000


class TestRepositoryContract:
    async def test_history_is_returned_oldest_first(
        self, database: Database, integration_settings: Settings
    ) -> None:
        """A prompt fed newest-first makes the model believe the landlord just
        said the opposite of what they said."""
        _, session_id = await _seed(database, integration_settings)
        now = datetime.now(UTC)

        async with database.session() as session:
            repository = SqlAlchemyConversationTurnRepository(session)
            for index, intent in enumerate(("FIRST", "SECOND", "THIRD")):
                await repository.add(
                    ConversationTurn(
                        session_id=session_id,
                        message_id=None,
                        direction="INBOUND",
                        intent=intent,
                        confidence=0.9,
                        used_llm=False,
                        latency_ms=0,
                        occurred_at=now + timedelta(seconds=index),
                    )
                )
            history = await repository.recent(session_id, limit=3)
            limited = await repository.recent(session_id, limit=2)

        assert [turn.intent for turn in history] == ["FIRST", "SECOND", "THIRD"]
        assert [turn.intent for turn in limited] == ["SECOND", "THIRD"]

    async def test_the_turn_count_reflects_only_this_session(
        self, database: Database, integration_settings: Settings
    ) -> None:
        _, session_id = await _seed(database, integration_settings)
        other_id = uuid.uuid4()

        async with database.session() as session:
            repository = SqlAlchemyConversationTurnRepository(session)
            for target in (session_id, other_id):
                await repository.add(
                    ConversationTurn(
                        session_id=target,
                        message_id=None,
                        direction="INBOUND",
                        intent="SUPPORT",
                        confidence=0.9,
                        used_llm=False,
                        latency_ms=0,
                        occurred_at=NOW,
                    )
                )
            history = await repository.recent(session_id)

        assert len(history) == 1


class TestUnrelatedRow:
    async def test_a_message_with_no_session_is_analysed_nothing(
        self, database: Database, integration_settings: Settings
    ) -> None:
        """A row inserted by an older handler, never linked to a conversation:
        the pipeline must report it, not invent a session for it."""
        now = datetime.now(UTC)
        message_id = uuid.uuid4()
        async with database.session() as session:
            session.add(
                InboundMessageRow(
                    id=message_id,
                    external_message_id="BAIL_ORPHAN",
                    provider="baileys",
                    conversation_ref="orphan@s.whatsapp.net",
                    sender_phone=PHONE,
                    sender_user_id=None,
                    session_id=None,
                    message_kind="TEXT",
                    text="orphelin",
                    media_object_key=None,
                    processed=False,
                    provider_timestamp=NOW,
                    received_at=now,
                    metadata_json={},
                )
            )
            await session.commit()

        report, _ = await _analyse(database, message_id, uuid.uuid4())

        assert report.reason == "session_not_found"


class TestEventStaging:
    async def test_ingestion_requests_the_analysis_in_the_same_transaction(
        self, database: Database, integration_settings: Settings
    ) -> None:
        """Both events must be durable together: a stored message nobody
        analyses is a defect no retry can repair."""
        message_id, session_id = await _seed(database, integration_settings)

        async with database.session() as session:
            records = list(
                (
                    await session.execute(
                        select(OutboxRecord).where(
                            OutboxRecord.aggregate_id == message_id
                        )
                    )
                )
                .scalars()
            )
        event_types = [record.event_type for record in records]
        assert event_types == ["WhatsAppMessageIngested", "ConversationAnalysisRequested"]
        request = next(r for r in records if r.event_type == "ConversationAnalysisRequested")
        assert request.topic == "ai.requests"
        assert request.payload["metadata"]["message_id"] == str(message_id)
        assert request.payload["metadata"]["session_id"] == str(session_id)
        assert "appartement" not in str(request.payload)
