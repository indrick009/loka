"""The decision-model pilot: asked on every turn, stored, never shown.

Three promises are checked here. The Jev adapter speaks the ``/systemone``
contract and turns every failure into a typed error. The shadow stores the
model's reading next to what the pipeline did — failures included. And the
pilot can never change or break the user's reply.
"""

from __future__ import annotations

import asyncio
import json
import uuid
from datetime import UTC, datetime
from decimal import Decimal
from typing import Any

import httpx
import pytest
from tests.unit.ai.test_analyse_conversation_message import (
    FakeLedger,
    FakeUnitOfWork,
    StubModel,
    _model_result,
    build,
)

from loka.bounded_contexts.ai.application.ports import (
    ChoiceAnswer,
    ChoiceQuestion,
    Decisions,
    ShadowDecision,
)
from loka.bounded_contexts.ai.application.services.decision_shadow import (
    TURN_QUESTIONS,
    DecisionShadow,
    turn_state,
)
from loka.bounded_contexts.ai.application.use_cases.analyse_conversation_message import (
    AnalysisCommand,
)
from loka.bounded_contexts.ai.infrastructure.jev import JevDecisionModel
from loka.bounded_contexts.identity.domain.value_objects.phone_number import PhoneNumber
from loka.bounded_contexts.messaging.domain.entities.conversation_session import (
    ConversationSession,
    FlowName,
    FlowStep,
)
from loka.shared.domain.errors import ExternalServiceUnavailable
from loka.shared.infrastructure.config.settings import AISettings

pytestmark = pytest.mark.unit

NOW = datetime(2026, 10, 14, 9, 0, tzinfo=UTC)
QUESTION = {"act": ChoiceQuestion(instructions="what?", options={"a": "first", "b": "second"})}


def _session() -> ConversationSession:
    session = ConversationSession(
        session_id=uuid.uuid4(),
        user_id=uuid.uuid4(),
        phone=PhoneNumber.normalize("+237699123456"),
        now=NOW,
    )
    session.start_flow(FlowName.PROPERTY_SEARCH, now=NOW, first_step=FlowStep.COLLECT_PRICE)
    session.context.update(
        {"property_type": "STUDIO", "next_question": "ask.search_budget", "_shown": [1]}
    )
    return session


def _decisions(**choices: str) -> Decisions:
    return Decisions(
        answers={
            name: ChoiceAnswer(choice=choice, confidence=0.9, probabilities={choice: 0.9})
            for name, choice in choices.items()
        },
        model="typesafe/jev-1.13",
        input_tokens=700,
        latency_ms=800,
        cost_usd=Decimal("0.00003"),
    )


class StubDecisionModel:
    def __init__(
        self,
        *,
        answer: Decisions | None = None,
        error: Exception | None = None,
        delay: float = 0.0,
    ) -> None:
        self.answer = answer or _decisions(act="slot_answer")
        self.error = error
        self.delay = delay

    @property
    def model(self) -> str:
        return "jev"

    async def decide(self, *, state: Any, questions: Any) -> Decisions:
        if self.delay:
            await asyncio.sleep(self.delay)
        if self.error is not None:
            raise self.error
        return self.answer


class FakeShadowLog:
    def __init__(self, *, fail: bool = False) -> None:
        self.entries: list[ShadowDecision] = []
        self.fail = fail

    async def record(self, entry: ShadowDecision) -> None:
        if self.fail:
            raise RuntimeError("database gone")
        self.entries.append(entry)


def _shadow(
    model: StubDecisionModel, *, log: FakeShadowLog | None = None, timeout: float = 2.0
) -> tuple[DecisionShadow, FakeShadowLog, FakeLedger, FakeUnitOfWork]:
    uow, ledger, shadow_log = FakeUnitOfWork(), FakeLedger(), log or FakeShadowLog()
    shadow = DecisionShadow(
        uow,
        model=model,
        log=shadow_log,
        usage=ledger,
        provider="openrouter",
        timeout_seconds=timeout,
    )
    return shadow, shadow_log, ledger, uow


async def _run(shadow: DecisionShadow, session: ConversationSession) -> None:
    pending = shadow.start(session, message_id=uuid.uuid4(), text="60 mille")
    assert pending is not None
    await shadow.finish(
        pending,
        intent="COLLECT_PRICE",
        accepted=True,
        used_llm=False,
        next_step="AWAITING_MATCH",
        now=NOW,
    )


def _jev(handler: Any, config: AISettings | None = None) -> tuple[
    JevDecisionModel, list[httpx.Request]
]:
    seen: list[httpx.Request] = []

    def record(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return handler(request)

    config = config or AISettings(api_key="test-key")
    client = httpx.AsyncClient(base_url=config.base_url, transport=httpx.MockTransport(record))
    return JevDecisionModel(config, client=client), seen


class TestTheJevAdapter:
    async def test_it_speaks_the_systemone_contract(self) -> None:
        model, seen = _jev(
            lambda _: httpx.Response(
                200,
                json={
                    "model": "typesafe/jev-1.13-20260917",
                    "answers": {
                        "act": {
                            "type": "choice",
                            "choice": "b",
                            "confidence": 0.8,
                            "probabilities": {"a": 0.1, "b": 0.9},
                        }
                    },
                    "usage": {"input_tokens": 737, "output_tokens": 204, "cost": 3.0954e-05},
                },
            )
        )

        decisions = await model.decide(state={"user_message": "hi"}, questions=QUESTION)

        request = seen[0]
        assert request.url.path == "/api/v1/systemone"
        assert request.headers["Authorization"] == "Bearer test-key"
        body = json.loads(request.content)
        assert body["model"] == "~typesafe/jev-latest"
        assert body["questions"]["act"] == {
            "type": "choice",
            "instructions": "what?",
            "criteria": {"a": "first", "b": "second"},
        }
        assert decisions.answers["act"].choice == "b"
        assert decisions.answers["act"].probabilities == {"a": 0.1, "b": 0.9}
        assert decisions.model == "typesafe/jev-1.13-20260917"
        assert decisions.input_tokens == 737
        assert decisions.cost_usd == Decimal("3.0954e-05")

    @pytest.mark.parametrize(
        "response",
        [
            httpx.Response(529, json={"error": "overloaded"}),
            httpx.Response(200, json={"unexpected": True}),
            httpx.Response(200, text="not json"),
        ],
    )
    async def test_a_bad_answer_is_a_typed_error(self, response: httpx.Response) -> None:
        model, _ = _jev(lambda _: response)

        with pytest.raises(ExternalServiceUnavailable):
            await model.decide(state={}, questions=QUESTION)

    async def test_without_a_key_nothing_is_sent(self) -> None:
        model, seen = _jev(lambda _: httpx.Response(200), AISettings())

        with pytest.raises(ExternalServiceUnavailable):
            await model.decide(state={}, questions=QUESTION)
        assert seen == []


class TestTheShadow:
    def test_the_state_names_the_task_and_hides_the_bookkeeping(self) -> None:
        """Without the active task a bare "60 mille" names no service at all."""
        state = turn_state(_session(), "60 mille")

        assert state["active_task"] == "housing: tenant search"
        assert state["kora_is_waiting_for"] is not None
        assert state["known_facts"] == {"property_type": "STUDIO"}
        assert state["user_message"] == "60 mille"

    def test_every_question_offers_koras_own_options(self) -> None:
        assert set(TURN_QUESTIONS) == {"act", "role", "domain", "neighbourhood_change"}
        assert "follow_up" in TURN_QUESTIONS["act"].options

    async def test_the_answer_is_stored_next_to_what_the_pipeline_did(self) -> None:
        shadow, log, ledger, uow = _shadow(StubDecisionModel())

        await _run(shadow, _session())

        entry = log.entries[0]
        assert entry.decisions is not None
        assert entry.decisions.answers["act"].choice == "slot_answer"
        assert entry.error is None
        assert entry.step_before == "COLLECT_PRICE"
        assert entry.pending_question == "ask.search_budget"
        assert entry.pipeline_intent == "COLLECT_PRICE"
        assert entry.pipeline_next_step == "AWAITING_MATCH"
        # Budgeted like any other call.
        assert ledger.entries[0].use_case == "decision_shadow"
        assert ledger.entries[0].cost_usd == Decimal("0.00003")
        assert uow.commits == 1

    async def test_a_failure_is_stored_too_and_costs_nothing(self) -> None:
        """A pilot that kept only its successes would overstate itself."""
        shadow, log, ledger, _ = _shadow(
            StubDecisionModel(error=ExternalServiceUnavailable("overloaded"))
        )

        await _run(shadow, _session())

        assert log.entries[0].decisions is None
        assert log.entries[0].error == "overloaded"
        assert ledger.entries == []

    async def test_a_slow_model_is_recorded_as_a_timeout(self) -> None:
        shadow, log, _, _ = _shadow(StubDecisionModel(delay=1.0), timeout=0.05)

        await _run(shadow, _session())

        assert log.entries[0].error == "timeout"

    async def test_its_own_bookkeeping_failing_never_raises(self) -> None:
        shadow, _, _, _ = _shadow(StubDecisionModel(), log=FakeShadowLog(fail=True))

        await _run(shadow, _session())

    def test_an_empty_message_is_not_asked_about(self) -> None:
        shadow, _, _, _ = _shadow(StubDecisionModel())

        assert shadow.start(_session(), message_id=uuid.uuid4(), text="  ") is None


class TestThePipelineWithTheShadow:
    async def test_the_reply_is_the_same_with_a_broken_decision_model(self) -> None:
        """The pilot reads every turn and decides none of them."""
        texts = []
        for model in (StubDecisionModel(), StubDecisionModel(error=RuntimeError("boom"))):
            session = _session()
            uow, message_id = FakeUnitOfWork(), uuid.uuid4()
            shadow, log, _, _ = _shadow(model)
            use_case, _, _, _ = build(
                uow,
                session,
                message_id,
                text="60 mille",
                model=StubModel(
                    result=_model_result(intent="COLLECT_PRICE", entities={"price": 60_000})
                ),
            )
            use_case._shadow = shadow

            report = await use_case.execute(
                AnalysisCommand(message_id=message_id, session_id=session.id), now=NOW
            )

            assert report.accepted is True
            assert log.entries[0].message_id == message_id
            assert log.entries[0].pipeline_intent == report.intent
            texts.append(uow.events.staged[0].text)
        assert texts[0] == texts[1]
