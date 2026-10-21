"""The three moves that surround the unlock: offer, reveal, and failure.

Each is driven by something other than the tenant's own message — a landlord's
answer, a provider's callback — so each must move the right session and stage
exactly one platform sentence, without ever inventing a listing or a number.
"""

from __future__ import annotations

import uuid
from collections.abc import Iterable
from datetime import UTC, datetime
from typing import Any

import pytest

from loka.bounded_contexts.ai.application.ports import UnlockedContact
from loka.bounded_contexts.ai.application.services.unlock_copy import (
    PAYMENT_QUESTION_KEY,
    UNLOCK_KEY,
)
from loka.bounded_contexts.ai.application.use_cases.handle_availability_answer import (
    HandleAvailabilityAnswerUseCase,
)
from loka.bounded_contexts.ai.application.use_cases.present_payment_failure import (
    PresentPaymentFailureUseCase,
)
from loka.bounded_contexts.ai.application.use_cases.reveal_unlocked_contact import (
    RevealUnlockedContactUseCase,
)
from loka.bounded_contexts.identity.domain.value_objects.phone_number import PhoneNumber
from loka.bounded_contexts.messaging.domain.entities.conversation_session import (
    ConversationSession,
    FlowName,
    FlowStep,
)
from loka.shared.application.unit_of_work import UnitOfWork

pytestmark = pytest.mark.unit

NOW = datetime(2026, 3, 1, 9, 0, tzinfo=UTC)
TENANT_PHONE = "+237699123456"
LANDLORD_PHONE = "+237658528664"
PROPERTY_ID = uuid.uuid4()
LANDLORD_ID = uuid.uuid4()


class FakeEvents:
    def __init__(self) -> None:
        self.staged: list[Any] = []

    def stage(self, events: Iterable[Any], *, correlation_id: str) -> None:
        self.staged.extend(events)

    async def flush(self) -> None:
        return None


class FakeSessions:
    def __init__(self, session: ConversationSession | None) -> None:
        self.session = session
        self.saved: list[int | None] = []

    async def latest_by_user(self, user_id: uuid.UUID) -> ConversationSession | None:
        return self.session

    async def save(
        self, session: ConversationSession, *, expected_revision: int | None = None
    ) -> None:
        self.saved.append(expected_revision)


class FakeUnitOfWork(UnitOfWork):
    def __init__(self) -> None:
        self._events = FakeEvents()
        self.commits = 0

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
    def events(self) -> FakeEvents:
        return self._events

    def collect(self, aggregate: Any) -> None:
        return None

    def repository(self, name: str) -> Any:
        raise KeyError(name)


class FakeContacts:
    def __init__(self, contact: UnlockedContact | None) -> None:
        self.contact = contact

    async def for_listing(
        self, *, property_id: uuid.UUID, landlord_id: uuid.UUID, language: str
    ) -> UnlockedContact | None:
        return self.contact


def a_tenant_waiting_on_availability(*, shown: bool = False) -> ConversationSession:
    session = ConversationSession(
        session_id=uuid.uuid4(),
        user_id=uuid.uuid4(),
        phone=PhoneNumber.normalize(TENANT_PHONE),
        now=NOW,
    )
    session.start_flow(FlowName.PROPERTY_SEARCH, now=NOW, first_step=FlowStep.AWAITING_MATCH)
    patch: dict[str, object] = {}
    if shown:
        patch["_shown_listings"] = [str(PROPERTY_ID)]
    session.transition(FlowStep.AWAITING_AVAILABILITY, now=NOW, context_patch=patch)
    return session


class TestTheLandlordConfirms:
    async def test_confirmation_offers_the_unlock_and_keeps_the_pair(self) -> None:
        session = a_tenant_waiting_on_availability()
        uow, sessions = FakeUnitOfWork(), FakeSessions(session)

        handled = await HandleAvailabilityAnswerUseCase(uow, sessions).execute(  # type: ignore[arg-type]
            tenant_id=session.user_id,
            property_id=PROPERTY_ID,
            landlord_id=LANDLORD_ID,
            confirmed=True,
            correlation_id="c",
            now=NOW,
        )

        assert handled is True
        assert session.step is FlowStep.CONFIRM_PAYMENT
        assert session.context[UNLOCK_KEY] == {
            "property_id": str(PROPERTY_ID),
            "landlord_id": str(LANDLORD_ID),
        }
        assert session.context["next_question"] == PAYMENT_QUESTION_KEY
        [message] = uow.events.staged
        assert "1 000 FCFA" in message.text
        assert uow.commits == 1

    async def test_the_landlords_answer_to_the_offer_comes_first(self) -> None:
        """§8: he accepted the tenant's terms; the tenant hears it before paying."""
        session = a_tenant_waiting_on_availability()
        uow, sessions = FakeUnitOfWork(), FakeSessions(session)

        await HandleAvailabilityAnswerUseCase(uow, sessions).execute(  # type: ignore[arg-type]
            tenant_id=session.user_id,
            property_id=PROPERTY_ID,
            landlord_id=LANDLORD_ID,
            confirmed=True,
            correlation_id="c",
            now=NOW,
            offer={"rent_xaf": 55_000, "asked_rent_xaf": 60_000},
            offer_reply={"outcome": "ACCEPTED"},
        )

        [message] = uow.events.staged
        assert message.text.startswith(
            "Bonne nouvelle : le propriétaire accepte 55 000 FCFA par mois."
        )
        assert "1 000 FCFA" in message.text

    async def test_refusal_asks_nothing_and_returns_to_the_list(self) -> None:
        session = a_tenant_waiting_on_availability(shown=True)
        uow, sessions = FakeUnitOfWork(), FakeSessions(session)

        await HandleAvailabilityAnswerUseCase(uow, sessions).execute(  # type: ignore[arg-type]
            tenant_id=session.user_id,
            property_id=PROPERTY_ID,
            landlord_id=LANDLORD_ID,
            confirmed=False,
            correlation_id="c",
            now=NOW,
        )

        assert session.step is FlowStep.COLLECT_PROPERTY_CHOICE
        assert UNLOCK_KEY not in session.context
        [message] = uow.events.staged
        assert "1 000 FCFA" not in message.text
        assert "n'est plus disponible" in message.text

    async def test_an_answer_after_the_tenant_moved_on_is_left_alone(self) -> None:
        session = a_tenant_waiting_on_availability()
        session.transition(FlowStep.AWAITING_MATCH, now=NOW)
        uow, sessions = FakeUnitOfWork(), FakeSessions(session)

        handled = await HandleAvailabilityAnswerUseCase(uow, sessions).execute(  # type: ignore[arg-type]
            tenant_id=session.user_id,
            property_id=PROPERTY_ID,
            landlord_id=LANDLORD_ID,
            confirmed=True,
            correlation_id="c",
            now=NOW,
        )

        assert handled is True
        assert session.step is FlowStep.AWAITING_MATCH
        assert uow.events.staged == []
        assert uow.commits == 0

    async def test_nobody_to_write_to_is_reported(self) -> None:
        uow, sessions = FakeUnitOfWork(), FakeSessions(None)

        handled = await HandleAvailabilityAnswerUseCase(uow, sessions).execute(  # type: ignore[arg-type]
            tenant_id=uuid.uuid4(),
            property_id=PROPERTY_ID,
            landlord_id=LANDLORD_ID,
            confirmed=True,
            correlation_id="c",
            now=NOW,
        )

        assert handled is False


def a_tenant_waiting_to_pay() -> ConversationSession:
    session = ConversationSession(
        session_id=uuid.uuid4(),
        user_id=uuid.uuid4(),
        phone=PhoneNumber.normalize(TENANT_PHONE),
        now=NOW,
    )
    session.start_flow(FlowName.PROPERTY_SEARCH, now=NOW, first_step=FlowStep.AWAITING_MATCH)
    session.transition(FlowStep.CONFIRM_PAYMENT, now=NOW)
    session.transition(FlowStep.AWAITING_PAYMENT, now=NOW)
    return session


class TestThePaymentSettles:
    async def test_the_number_and_the_map_are_sent_once_paid(self) -> None:
        session = a_tenant_waiting_to_pay()
        uow, sessions = FakeUnitOfWork(), FakeSessions(session)
        contacts = FakeContacts(
            UnlockedContact(
                landlord_phone=LANDLORD_PHONE,
                lodging="Studio - Jouvence, Yaoundé",
                point={"latitude": 3.86, "longitude": 11.51},
            )
        )

        revealed = await RevealUnlockedContactUseCase(
            uow, sessions, contacts=contacts  # type: ignore[arg-type]
        ).execute(
            tenant_id=session.user_id,
            property_id=PROPERTY_ID,
            landlord_id=LANDLORD_ID,
            correlation_id="c",
            now=NOW,
        )

        assert revealed is True
        assert session.step is FlowStep.DONE
        [message] = uow.events.staged
        assert "6 58 52 86 64" in message.text
        assert "maps.google.com" in message.text
        assert uow.commits == 1

    async def test_a_contact_that_cannot_be_assembled_is_refused(self) -> None:
        session = a_tenant_waiting_to_pay()
        uow, sessions = FakeUnitOfWork(), FakeSessions(session)

        revealed = await RevealUnlockedContactUseCase(
            uow, sessions, contacts=FakeContacts(None)  # type: ignore[arg-type]
        ).execute(
            tenant_id=session.user_id,
            property_id=PROPERTY_ID,
            landlord_id=LANDLORD_ID,
            correlation_id="c",
            now=NOW,
        )

        assert revealed is False
        assert uow.events.staged == []
        assert uow.commits == 0


class TestThePaymentFails:
    async def test_failure_returns_to_the_offer_and_keeps_the_pair(self) -> None:
        session = a_tenant_waiting_to_pay()
        session.context[UNLOCK_KEY] = {
            "property_id": str(PROPERTY_ID),
            "landlord_id": str(LANDLORD_ID),
        }
        uow, sessions = FakeUnitOfWork(), FakeSessions(session)

        handled = await PresentPaymentFailureUseCase(uow, sessions).execute(  # type: ignore[arg-type]
            tenant_id=session.user_id, correlation_id="c", now=NOW
        )

        assert handled is True
        assert session.step is FlowStep.CONFIRM_PAYMENT
        assert UNLOCK_KEY in session.context
        [message] = uow.events.staged
        assert "n'a pas abouti" in message.text
        assert uow.commits == 1

    async def test_a_tenant_who_moved_on_is_not_dragged_back(self) -> None:
        session = a_tenant_waiting_to_pay()
        session.transition(FlowStep.AWAITING_MATCH, now=NOW)
        uow, sessions = FakeUnitOfWork(), FakeSessions(session)

        handled = await PresentPaymentFailureUseCase(uow, sessions).execute(  # type: ignore[arg-type]
            tenant_id=session.user_id, correlation_id="c", now=NOW
        )

        assert handled is True
        assert session.step is FlowStep.AWAITING_MATCH
        assert uow.events.staged == []
