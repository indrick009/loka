"""The message that comes back to a tenant, and the step it leaves him on.

The choice step existed and nothing led to it: the list is what writes the ids
his "2" is read against. These tests pin that the message and the session agree
— the lines he reads are the listings his number names.
"""

from __future__ import annotations

import uuid
from collections.abc import Iterable
from datetime import UTC, datetime
from typing import Any

import pytest

from loka.bounded_contexts.ai.application.services.match_proposals import (
    SHOWN_LISTINGS_KEY,
    match_proposals_text,
)
from loka.bounded_contexts.ai.application.use_cases.present_match_proposals import (
    PresentMatchProposalsUseCase,
)
from loka.bounded_contexts.identity.domain.value_objects.phone_number import PhoneNumber
from loka.bounded_contexts.messaging.domain.entities.conversation_session import (
    ConversationSession,
    FlowName,
    FlowStep,
)
from loka.shared.application.unit_of_work import UnitOfWork

pytestmark = pytest.mark.unit

NOW = datetime(2026, 10, 15, 9, 0, tzinfo=UTC)

JOUVENCE = {
    "property_id": str(uuid.uuid4()),
    "property_type": "STUDIO",
    "city": "Yaoundé",
    "neighbourhood": "Jouvence",
    "rent_xaf": 40_000,
}
BIYEM_ASSI = {**JOUVENCE, "property_id": str(uuid.uuid4()), "neighbourhood": "Biyem-Assi"}


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


def a_tenant_waiting(flow: FlowName = FlowName.PROPERTY_SEARCH) -> ConversationSession:
    session = ConversationSession(
        session_id=uuid.uuid4(),
        user_id=uuid.uuid4(),
        phone=PhoneNumber.normalize("+237658528664"),
        now=NOW,
    )
    session.start_flow(flow, now=NOW, first_step=FlowStep.AWAITING_MATCH)
    return session


async def present(
    session: ConversationSession | None, proposals: list[dict[str, Any]]
) -> tuple[bool, FakeUnitOfWork, FakeSessions]:
    uow, sessions = FakeUnitOfWork(), FakeSessions(session)
    sent = await PresentMatchProposalsUseCase(uow, sessions).execute(  # type: ignore[arg-type]
        tenant_id=uuid.uuid4(), proposals=proposals, correlation_id="c", now=NOW
    )
    return sent, uow, sessions


class TestTheTenantIsWrittenTo:
    async def test_the_list_and_the_choice_step_agree(self) -> None:
        session = a_tenant_waiting()

        sent, uow, sessions = await present(session, [JOUVENCE, BIYEM_ASSI])

        assert sent is True
        assert session.step is FlowStep.COLLECT_PROPERTY_CHOICE
        assert session.context[SHOWN_LISTINGS_KEY] == [
            JOUVENCE["property_id"],
            BIYEM_ASSI["property_id"],
        ]
        assert session.context["next_question"] == "ask.search_choice"
        assert sessions.saved, "the session move must be persisted"
        [message] = uow.events.staged
        assert message.recipient_phone == "+237658528664"
        assert "1. Studio - Jouvence, Yaoundé - 40 000 FCFA/mois" in message.text
        assert "2. Studio - Biyem-Assi, Yaoundé" in message.text
        assert uow.commits == 1

    async def test_a_tenant_busy_elsewhere_keeps_his_form(self) -> None:
        session = a_tenant_waiting(FlowName.PROPERTY_CREATION)

        sent, uow, sessions = await present(session, [JOUVENCE])

        assert sent is True
        assert session.step is FlowStep.AWAITING_MATCH
        assert SHOWN_LISTINGS_KEY not in session.context
        assert sessions.saved == []
        assert len(uow.events.staged) == 1

    async def test_nobody_to_write_to_sends_nothing(self) -> None:
        sent, uow, _ = await present(None, [JOUVENCE])

        assert sent is False
        assert uow.events.staged == []


class TestThePhotos:
    async def test_every_stored_photo_is_sent_after_the_list(self) -> None:
        session = a_tenant_waiting()
        lodging = {**JOUVENCE, "photo_object_keys": ["p/1/a.jpg", "p/1/b.jpg"]}

        sent, uow, _ = await present(session, [lodging])

        assert sent is True
        text, first, second = uow.events.staged
        assert text.media_object_key is None
        assert first.media_object_key == "p/1/a.jpg"
        assert second.media_object_key == "p/1/b.jpg"

    async def test_the_caption_is_the_numbered_line_the_reply_names(self) -> None:
        session = a_tenant_waiting()
        lodging = {**JOUVENCE, "photo_object_keys": ["p/1/a.jpg", "p/1/b.jpg"]}

        _, uow, _ = await present(session, [lodging])

        line = "1. Studio - Jouvence, Yaoundé - 40 000 FCFA/mois"
        assert uow.events.staged[1].text == f"{line} (photo 1/2)"
        assert uow.events.staged[2].text == f"{line} (photo 2/2)"

    async def test_a_lodging_with_no_photo_adds_no_message(self) -> None:
        session = a_tenant_waiting()

        _, uow, _ = await present(session, [JOUVENCE])

        assert len(uow.events.staged) == 1
        assert uow.events.staged[0].media_object_key is None

    async def test_each_lodging_keeps_its_own_number(self) -> None:
        session = a_tenant_waiting()
        first = {**JOUVENCE, "photo_object_keys": ["p/1/a.jpg"]}
        second = {**BIYEM_ASSI, "photo_object_keys": ["p/2/a.jpg"]}

        _, uow, _ = await present(session, [first, second])

        assert uow.events.staged[1].media_object_key == "p/1/a.jpg"
        assert uow.events.staged[1].text.startswith("1. Studio - Jouvence")
        assert uow.events.staged[2].media_object_key == "p/2/a.jpg"
        assert uow.events.staged[2].text.startswith("2. Studio - Biyem-Assi")


class TestTheMessage:
    def test_one_lodging_is_announced_as_one(self) -> None:
        text = match_proposals_text([JOUVENCE], "fr")

        assert text.startswith("Bonne nouvelle : j'ai trouvé un logement")
        assert "Répondez avec le numéro" in text

    def test_english_thread_gets_english(self) -> None:
        text = match_proposals_text([JOUVENCE, BIYEM_ASSI], "en")

        assert "I found 2 places" in text
        assert "40 000 FCFA/month" in text
