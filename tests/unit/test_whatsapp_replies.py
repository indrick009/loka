"""Outbound copy: a landlord must never see a key.

The analysis picks the question and hands over its stable ``question_key``;
this module words it. An unrendered key reaching a thread is a variable name
in a conversation, so every path is pinned to return a sentence.
"""

from __future__ import annotations

import pytest

from loka.interfaces.worker.whatsapp_replies import (
    ACK_TEXT,
    CLARIFY_TEXT,
    NO_QUESTION_TEXT,
    QUESTION_TEXT,
    render_reply,
)

pytestmark = pytest.mark.unit

PROPERTY_FLOW = {
    "ask.property_type",
    "ask.location",
    "ask.rent",
    "ask.features",
    "ask.charges",
    "ask.minimum_duration",
    "ask.availability",
    "ask.conditions",
    "ask.confirm_property",
    "confirm.published",
    # The tenant's side of the same conversation.
    "ask.search_type",
    "ask.search_location",
    "ask.search_budget",
    "ask.search_criteria",
    "ask.search_results",
    "ask.search_choice",
}


class TestQuestions:
    def test_the_whole_property_flow_has_a_sentence(self) -> None:
        assert set(QUESTION_TEXT) >= PROPERTY_FLOW

    @pytest.mark.parametrize("key", sorted(QUESTION_TEXT))
    def test_the_sentence_is_prose_not_a_variable_name(self, key: str) -> None:
        sentence = render_reply(reply_kind="QUESTION", question_key=key)

        assert sentence == QUESTION_TEXT[key]
        assert sentence.strip()
        assert key not in sentence
        assert not sentence.startswith("ask.")
        assert "_" not in sentence


class TestUnknownQuestions:
    def test_a_missing_key_degrades_to_the_clarification(self) -> None:
        assert render_reply(reply_kind="QUESTION", question_key=None) == CLARIFY_TEXT

    def test_an_untranslated_key_degrades_to_the_clarification(self) -> None:
        sentence = render_reply(reply_kind="QUESTION", question_key="ask.price_per_m2")

        assert sentence == CLARIFY_TEXT

    def test_an_unknown_kind_degrades_to_the_clarification(self) -> None:
        assert render_reply(reply_kind="IGNORED", question_key="ask.rent") == CLARIFY_TEXT

    def test_the_clarification_is_a_sentence_the_landlord_can_act_on(self) -> None:
        assert CLARIFY_TEXT.strip()
        assert "CLARIFY" not in CLARIFY_TEXT


class TestAcknowledgements:
    def test_a_bare_ack_is_the_default(self) -> None:
        assert render_reply(reply_kind="ACK", question_key=None) == ACK_TEXT

    def test_an_intent_without_a_question_gets_its_own_sentence(self) -> None:
        for intent, sentence in NO_QUESTION_TEXT.items():
            assert render_reply(reply_kind="ACK", question_key=None, intent=intent) == sentence

    def test_an_unknown_intent_falls_back_to_the_bare_ack(self) -> None:
        assert render_reply(reply_kind="ACK", question_key=None, intent="SMALLTALK") == ACK_TEXT
