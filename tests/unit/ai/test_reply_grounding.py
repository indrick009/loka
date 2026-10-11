"""What the reply writer is allowed to know before it writes.

Two prompts can produce the sentence a user reads. The analysis prompt receives
``known_facts`` — everything the session holds. The reply prompt did not: it saw
only the facts extracted from the current message. So the assistant remembered
on one path and forgot on the other, and the forgetting was not quiet. Asked
"tu te rappelles du prix que j'ai donné ?" it answered "je n'ai pas enregistré
de prix", then two turns later invented "vous avez mentionné 150 000 FCFA" for a
landlord who had said 50 000 (**D8**).

The same blindness produced the other half: asked to request the location while
``property_type`` was already known, the model asked for the property type
instead — a question already answered (**D7**).

What is *not* fixed here, on purpose: ``TurnSummary`` still carries no message
text. That is a deliberate data-minimisation decision — ``conversation_turns``
keeps no copy, so history cannot leak more than the shape of the conversation —
and the facts are enough to ground a reply. See ``docs/conversation-defects-v1.md``.
"""

from __future__ import annotations

import json
from typing import Any

import pytest

from loka.bounded_contexts.ai.application.services.prompts import build_reply_prompt
from loka.bounded_contexts.ai.domain.services.fact_extraction import ExtractedFacts
from loka.bounded_contexts.messaging.domain.entities.conversation_session import (
    FlowName,
)

pytestmark = pytest.mark.unit


def an_outcome(**overrides: Any) -> dict[str, Any]:
    """The accepted outcome of a turn, as ``_compose_reply`` hands it over.

    Three plain values, not the use case's verdict object: the prompt builder
    takes what it reads, so a test of the prompt needs nothing from the state
    machine.
    """
    base: dict[str, Any] = {
        "intent": "COLLECT_LOCATION",
        "accepted": True,
        "accepted_facts": ExtractedFacts(
            facts={"location": {"city": "Yaoundé"}}
        ).as_context(),
    }
    base.update(overrides)
    return base


def payload_of(
    *, known_facts: dict[str, Any], next_key: str | None = "ask.rent"
) -> dict[str, Any]:
    prompt = build_reply_prompt(
        text="c'est à Biyem-Assi",
        language="fr",
        **an_outcome(),
        next_key=next_key,
        history=[],
        flow=FlowName.PROPERTY_CREATION,
        known_facts=known_facts,
    )
    return dict(json.loads(prompt.user))


def as_text(body: dict[str, Any]) -> str:
    return json.dumps(body, ensure_ascii=False)


class TestTheWriterSeesWhatTheSessionKnows:
    def test_facts_stored_on_earlier_turns_reach_the_writer(self) -> None:
        """The D8 fix: a price given three turns ago is still recallable."""
        body = payload_of(known_facts={"rent": 50_000, "property_type": "STUDIO"})

        assert "50000" in as_text(body).replace(" ", "")
        assert "STUDIO" in as_text(body)

    def test_the_current_turns_facts_are_there_too(self) -> None:
        body = payload_of(known_facts={"rent": 50_000})

        assert "Yaoundé" in as_text(body)

    def test_an_empty_session_grounds_on_nothing_rather_than_failing(self) -> None:
        body = payload_of(known_facts={})

        assert body["ask_about"] is not None


class TestInternalStateNeverReachesTheProvider:
    @pytest.mark.parametrize("internal", ["next_question", "abort_reason", "_private"])
    def test_bookkeeping_keys_are_stripped(self, internal: str) -> None:
        """A prompt is not a dump of the row.

        ``next_question`` is a question key, not a fact: handed to the writer it
        reads as something the user said. Keys starting with an underscore are
        platform bookkeeping and cost tokens for nothing.
        """
        body = payload_of(known_facts={internal: "ask.standing", "rent": 50_000})

        assert internal not in as_text(body)

    def test_real_facts_survive_the_stripping(self) -> None:
        body = payload_of(known_facts={"next_question": "ask.rent", "rent": 50_000})

        assert "50000" in as_text(body).replace(" ", "")


class TestTheWriterIsToldWhichQuestionToAsk:
    def test_the_decided_question_is_named(self) -> None:
        body = payload_of(known_facts={}, next_key="ask.rent")

        assert body["ask_about"] is not None

    def test_no_question_means_no_question_is_invited(self) -> None:
        """A turn with nothing to ask must not have one invented for it."""
        body = payload_of(known_facts={"rent": 50_000}, next_key=None)

        assert body["ask_about"] is None
