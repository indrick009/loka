"""The 1 000 FCFA turn: what a "oui", a number and a "non" each do.

The unlock is the one exchange where a misread costs money, so the reading is a
rule and the model never sees it. These tests pin the four answers and the step
each leaves behind, and that nothing is charged for an answer that is not a yes.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime
from typing import Any

import pytest
from tests.unit.ai.test_analyse_conversation_message import (
    FakeInboundRepository,
    FakeLedger,
    FakeSessionRepository,
    FakeTurnRepository,
    FakeUnitOfWork,
)

from loka.bounded_contexts.ai.application.ports import UnlockRequestOutcome
from loka.bounded_contexts.ai.application.services.unlock_copy import (
    PAYMENT_QUESTION_KEY,
    UNLOCK_KEY,
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

pytestmark = pytest.mark.unit

NOW = datetime(2026, 3, 1, 9, 0, tzinfo=UTC)
PHONE = "+237699123456"
PROPERTY_ID = uuid.uuid4()
LANDLORD_ID = uuid.uuid4()


class RecordingUnlock:
    """The 1 000 FCFA port, reduced to what the turn needs to decide."""

    def __init__(
        self, outcome: UnlockRequestOutcome = UnlockRequestOutcome.REQUESTED
    ) -> None:
        self.outcome = outcome
        self.requests: list[dict[str, Any]] = []

    async def request(
        self,
        *,
        tenant_id: uuid.UUID,
        property_id: uuid.UUID,
        landlord_id: uuid.UUID,
        payer_phone: str,
        now: datetime,
    ) -> UnlockRequestOutcome:
        self.requests.append(
            {
                "tenant_id": tenant_id,
                "property_id": property_id,
                "landlord_id": landlord_id,
                "payer_phone": payer_phone,
                "now": now,
            }
        )
        return self.outcome


def a_tenant_at_the_offer(
    step: FlowStep = FlowStep.CONFIRM_PAYMENT,
    *,
    shown: bool = False,
) -> ConversationSession:
    session = ConversationSession(
        session_id=uuid.uuid4(),
        user_id=uuid.uuid4(),
        phone=PhoneNumber.normalize(PHONE),
        now=NOW,
    )
    session.start_flow(FlowName.PROPERTY_SEARCH, now=NOW, first_step=FlowStep.AWAITING_MATCH)
    patch: dict[str, object] = {
        UNLOCK_KEY: {"property_id": str(PROPERTY_ID), "landlord_id": str(LANDLORD_ID)}
    }
    if shown:
        patch["_shown_listings"] = [str(PROPERTY_ID)]
    session.transition(FlowStep.CONFIRM_PAYMENT, now=NOW, context_patch=patch)
    if step is not FlowStep.CONFIRM_PAYMENT:
        session.transition(step, now=NOW)
    return session


async def run(
    session: ConversationSession,
    *,
    text: str,
    outcome: UnlockRequestOutcome = UnlockRequestOutcome.REQUESTED,
) -> tuple[ConversationSession, RecordingUnlock, FakeUnitOfWork]:
    unlock = RecordingUnlock(outcome)
    uow = FakeUnitOfWork()
    message_id = uuid.uuid4()
    use_case = AnalyseConversationMessageUseCase(
        uow,
        inbound=FakeInboundRepository(
            rows={
                message_id: StoredInboundMessage(
                    message_id=message_id,
                    session_id=session.id,
                    sender_user_id=session.user_id,
                    processed=False,
                    text=text,
                )
            }
        ),
        sessions=FakeSessionRepository(store={session.id: session}),
        turns=FakeTurnRepository(),
        usage=FakeLedger(),
        model=None,
        response_model=None,
        unlock_payments=unlock,
    )
    await use_case.execute(
        AnalysisCommand(message_id=message_id, session_id=session.id), now=NOW
    )
    return session, unlock, uow


class TestTheAnswerIsReadByRule:
    async def test_yes_charges_the_thread_number(self) -> None:
        session, unlock, uow = await run(a_tenant_at_the_offer(), text="oui")

        assert [r["payer_phone"] for r in unlock.requests] == [PHONE]
        assert [r["property_id"] for r in unlock.requests] == [PROPERTY_ID]
        assert session.step is FlowStep.AWAITING_PAYMENT
        assert uow.commits >= 1

    async def test_another_number_is_the_one_charged(self) -> None:
        session, unlock, _ = await run(
            a_tenant_at_the_offer(), text="utilisez le 658 52 86 64"
        )

        assert [r["payer_phone"] for r in unlock.requests] == ["+237658528664"]
        assert session.step is FlowStep.AWAITING_PAYMENT

    async def test_no_charges_nothing_and_goes_back_to_the_list(self) -> None:
        session = a_tenant_at_the_offer(shown=True)

        session, unlock, uow = await run(session, text="non merci")

        assert unlock.requests == []
        assert session.step is FlowStep.COLLECT_PROPERTY_CHOICE
        assert uow.commits >= 1

    async def test_no_without_a_list_waits_for_a_match(self) -> None:
        session, unlock, _ = await run(a_tenant_at_the_offer(), text="non")

        assert unlock.requests == []
        assert session.step is FlowStep.AWAITING_MATCH

    async def test_unclear_reasks_and_stays_on_the_offer(self) -> None:
        session, unlock, _ = await run(a_tenant_at_the_offer(), text="peut-être")

        assert unlock.requests == []
        assert session.step is FlowStep.CONFIRM_PAYMENT
        assert session.context["next_question"] == PAYMENT_QUESTION_KEY

    async def test_already_unlocked_closes_the_unlock_without_charging(self) -> None:
        session, unlock, _ = await run(
            a_tenant_at_the_offer(),
            text="oui",
            outcome=UnlockRequestOutcome.ALREADY_UNLOCKED,
        )

        assert len(unlock.requests) == 1
        assert session.step is FlowStep.DONE
        assert UNLOCK_KEY not in session.context

    async def test_not_confirmed_returns_to_waiting(self) -> None:
        session, _, _ = await run(
            a_tenant_at_the_offer(),
            text="oui",
            outcome=UnlockRequestOutcome.NOT_CONFIRMED,
        )

        assert session.step is FlowStep.AWAITING_MATCH
        assert UNLOCK_KEY not in session.context

    async def test_not_confirmed_with_a_remaining_list_returns_to_it(self) -> None:
        session, _, _ = await run(
            a_tenant_at_the_offer(shown=True),
            text="oui",
            outcome=UnlockRequestOutcome.NOT_CONFIRMED,
        )

        assert session.step is FlowStep.COLLECT_PROPERTY_CHOICE

    async def test_a_failed_withdrawal_offers_a_retry(self) -> None:
        session, _, _ = await run(
            a_tenant_at_the_offer(), text="oui", outcome=UnlockRequestOutcome.FAILED
        )

        assert session.step is FlowStep.CONFIRM_PAYMENT
        assert UNLOCK_KEY in session.context  # kept, so "oui" retries this lodging

    async def test_a_waiting_withdrawal_is_not_moved_by_his_word(self) -> None:
        session, unlock, _ = await run(
            a_tenant_at_the_offer(FlowStep.AWAITING_PAYMENT), text="j'ai payé"
        )

        assert unlock.requests == []
        assert session.step is FlowStep.AWAITING_PAYMENT

    async def test_a_missing_booking_never_starts_a_withdrawal(self) -> None:
        session = a_tenant_at_the_offer()
        session.context.pop(UNLOCK_KEY)

        session, unlock, _ = await run(session, text="oui")

        assert unlock.requests == []
        assert session.step is FlowStep.CONFIRM_PAYMENT
