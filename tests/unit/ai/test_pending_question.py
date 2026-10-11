"""Which question a turn asks, and what the session then remembers.

Two defects meet here, and they share one cause: the question to ask was
computed twice, in two places, from different inputs.

**D4 — the dead end.** A turn that did not move the form ("merci", "oui", "tu te
rappelles du prix ?") produced no next step, so no question key, so the outbound
layer sent "C'est bien noté. Continuez quand vous voulez, je vous pose la
question suivante dès que j'ai besoin d'une précision." — and then asked
nothing. From the user's side the conversation was over, and no metric noticed.

**D5 — the stale pending question.** The stored ``next_question`` was only ever
written, never cleared, so it kept describing a question nobody had asked. The
next short answer was then bound to the wrong field, invisibly, because the
state looked populated.

See ``docs/conversation-defects-v1.md``.

The fix makes one decision in one place: the question this turn asks is the
form's next one, or failing that the one still open. The session records exactly
that, which is what keeps the two in step.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime

import pytest

from loka.bounded_contexts.ai.domain.services.fact_extraction import (
    question_key_for,
    question_to_ask,
)
from loka.bounded_contexts.identity.domain.value_objects.phone_number import PhoneNumber
from loka.bounded_contexts.messaging.domain.entities.conversation_session import (
    ConversationSession,
    FlowName,
    FlowStep,
)
from loka.interfaces.worker.whatsapp_replies import render_reply

pytestmark = pytest.mark.unit

NOW = datetime(2026, 3, 1, 9, 0, tzinfo=UTC)


def a_session(
    *,
    flow: FlowName = FlowName.PROPERTY_CREATION,
    step: FlowStep = FlowStep.COLLECT_STANDING,
    context: dict[str, object] | None = None,
) -> ConversationSession:
    session = ConversationSession(
        session_id=uuid.uuid4(),
        user_id=uuid.uuid4(),
        phone=PhoneNumber.normalize("+237699123456"),
        now=NOW,
        flow=flow,
    )
    session.step = step
    if context is not None:
        session.context = dict(context)
    return session


class TestTheQuestionThisTurnAsks:
    def test_the_forms_next_question_wins(self) -> None:
        asked = question_to_ask(
            next_step=FlowStep.COLLECT_LOCATION,
            flow=FlowName.PROPERTY_CREATION,
            pending="ask.standing",
        )

        assert asked == "ask.location"

    def test_a_turn_that_moved_nothing_re_asks_the_open_question(self) -> None:
        """The D4 fix: "merci" is answered, and the thread keeps going."""
        asked = question_to_ask(
            next_step=None, flow=FlowName.PROPERTY_CREATION, pending="ask.standing"
        )

        assert asked == "ask.standing"

    def test_nothing_is_asked_when_nothing_is_open(self) -> None:
        """A greeting on a fresh session has no question to repeat."""
        asked = question_to_ask(next_step=None, flow=FlowName.SUPPORT, pending=None)

        assert asked is None

    def test_the_flow_decides_the_wording_of_the_same_step(self) -> None:
        """A tenant and a landlord are asked opposite things at one step."""
        landlord = question_to_ask(
            next_step=FlowStep.COLLECT_PRICE,
            flow=FlowName.PROPERTY_CREATION,
            pending=None,
        )
        tenant = question_to_ask(
            next_step=FlowStep.COLLECT_PRICE,
            flow=FlowName.PROPERTY_SEARCH,
            pending=None,
        )

        assert landlord == "ask.rent"
        assert tenant == "ask.search_budget"
        assert landlord != tenant


class TestTheSessionRemembersWhatWasAsked:
    def test_the_asked_question_is_recorded(self) -> None:
        session = a_session()

        session.record_pending_question("ask.location")

        assert session.context["next_question"] == "ask.location"

    def test_asking_nothing_clears_the_previous_question(self) -> None:
        """The D5 fix.

        Leaving a question behind binds the next short answer to something
        nobody asked, and the state looks healthy while it happens.
        """
        session = a_session(context={"next_question": "ask.confirm_property"})

        session.record_pending_question(None)

        assert "next_question" not in session.context

    def test_clearing_an_empty_context_is_harmless(self) -> None:
        session = a_session()

        session.record_pending_question(None)

        assert "next_question" not in session.context

    def test_the_rest_of_the_context_is_untouched(self) -> None:
        session = a_session(
            context={"next_question": "ask.standing", "property_type": "STUDIO"}
        )

        session.record_pending_question(None)

        assert session.context["property_type"] == "STUDIO"


class TestTheMediaStepCanBeAsked:
    """D18: the one step a landlord must act on had no question at all."""

    def test_the_media_step_has_a_question_key(self) -> None:
        key = question_key_for(FlowStep.COLLECT_MEDIA, FlowName.PROPERTY_CREATION)

        assert key is not None

    @pytest.mark.parametrize("language", ["fr", "en"])
    def test_the_media_question_has_real_copy(self, language: str) -> None:
        """Without copy the landlord is told "noted" and never asked for photos."""
        key = question_key_for(FlowStep.COLLECT_MEDIA, FlowName.PROPERTY_CREATION)
        assert key is not None

        text = render_reply(
            reply_kind="QUESTION",
            question_key=key,
            intent="CREATE_PROPERTY",
            language=language,
        )

        assert "photo" in text.lower()
