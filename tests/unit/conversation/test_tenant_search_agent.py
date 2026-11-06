"""The Pydantic AI prototype: the model talks, the code still decides.

No network here. ``FunctionModel`` plays the model's part with scripted tool
calls, so what is pinned is the guard rails around any model: refused facts
stay out of the state, the search waits for the four questions, the listings
are the catalogue's words, and an amount nobody stated never reaches a user.
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

import pytest

pytest.importorskip("pydantic_ai")

from pydantic_ai.messages import (
    ModelMessage,
    ModelResponse,
    ToolCallPart,
    ToolReturnPart,
)
from pydantic_ai.models.function import AgentInfo, FunctionModel

from loka.bounded_contexts.ai.infrastructure.agent.demo_catalogue import (
    DemoCatalogue,
)
from loka.bounded_contexts.ai.infrastructure.agent.tenant_search import (
    TenantSearchAgent,
    amounts_in,
    missing_fields,
)
from loka.bounded_contexts.conversation.application.ports import (
    ConversationPropertyHit,
)

pytestmark = pytest.mark.unit

Step = Callable[[list[ModelMessage], AgentInfo], ModelResponse]


def _call(name: str, **args: Any) -> ModelResponse:
    return ModelResponse(parts=[ToolCallPart(tool_name=name, args=args)])


def _final(info: AgentInfo, act: str, reply: str) -> ModelResponse:
    return ModelResponse(
        parts=[ToolCallPart(tool_name=info.output_tools[0].name, args={"act": act, "reply": reply})]
    )


def _scripted(*steps: Step) -> tuple[FunctionModel, list[list[ModelMessage]]]:
    """A model that plays ``steps`` in order, and records what it was shown."""
    queue = list(steps)
    seen: list[list[ModelMessage]] = []

    def play(messages: list[ModelMessage], info: AgentInfo) -> ModelResponse:
        seen.append(list(messages))
        return queue.pop(0)(messages, info)

    return FunctionModel(play), seen


def _tool_returns(messages: list[ModelMessage]) -> list[Any]:
    return [
        part.content
        for message in messages
        for part in getattr(message, "parts", [])
        if isinstance(part, ToolReturnPart)
    ]


_FULL = {
    "property_type": "STUDIO",
    "location": {"city": "Yaoundé", "neighbourhood": "Étoug-ébé"},
    "rent": 60000,
    "advance_months": 4,
}


class TestRecordFacts:
    async def test_several_answers_in_one_message_are_all_stored(self) -> None:
        model, seen = _scripted(
            lambda m, i: _call(
                "record_facts",
                property_type="STUDIO",
                neighbourhoods=["Biyem-Assi"],
                max_rent_xaf=50000,
            ),
            lambda m, i: _final(i, "business_intent", "Combien de mois d'avance ?"),
        )
        turn = await TenantSearchAgent(model).reply(
            "je cherche un studio à Biyem-Assi vers 50mil", facts={}
        )

        assert turn.facts["property_type"] == "STUDIO"
        assert turn.facts["location"]["city"] == "Yaoundé"
        assert turn.facts["rent"] == 50000
        assert missing_fields(turn.facts) == ["advance_months"]
        returned = _tool_returns(seen[-1])[0]
        assert returned["still_missing"] == ["advance_months"]

    async def test_an_unknown_neighbourhood_is_refused_never_promoted_to_a_city(self) -> None:
        model, seen = _scripted(
            lambda m, i: _call("record_facts", neighbourhoods=["Zzzville"]),
            lambda m, i: _final(i, "slot_answer", "Zzzville, c'est dans quelle ville ?"),
        )
        turn = await TenantSearchAgent(model).reply("à Zzzville", facts={})

        assert "location" not in turn.facts
        assert "Zzzville" in _tool_returns(seen[-1])[0]["rejected"]["location"]

    async def test_a_bare_fifty_is_asked_back_not_stored_as_fifty_francs(self) -> None:
        model, _ = _scripted(
            lambda m, i: _call("record_facts", max_rent_xaf=50),
            lambda m, i: _final(i, "slot_answer", "Vous voulez dire 50 000 FCFA ?"),
        )
        turn = await TenantSearchAgent(model).reply("50", facts={"property_type": "ROOM"})

        assert "rent" not in turn.facts
        # Reading his number back in thousands is not an invented amount.
        assert turn.reply == "Vous voulez dire 50 000 FCFA ?"

    async def test_a_correction_replaces_the_old_value_and_keeps_the_rest(self) -> None:
        model, _ = _scripted(
            lambda m, i: _call("record_facts", max_rent_xaf=60000),
            lambda m, i: _final(i, "correction", "C'est noté, 60 000 FCFA au maximum."),
        )
        known = {"property_type": "STUDIO", "rent": 50000}
        turn = await TenantSearchAgent(model).reply("finalement 60mil", facts=known)

        assert turn.facts == {"property_type": "STUDIO", "rent": 60000}
        assert known["rent"] == 50000, "the caller's state is never mutated"


class TestSearch:
    async def test_the_search_waits_for_every_question(self) -> None:
        catalogue = DemoCatalogue.yaounde()
        model, seen = _scripted(
            lambda m, i: _call("search_listings"),
            lambda m, i: _final(i, "open_request", "Il me manque votre budget."),
        )
        turn = await TenantSearchAgent(model).reply(
            "montre-moi", facts={"property_type": "STUDIO"}, search=catalogue
        )

        assert turn.hits == ()
        assert _tool_returns(seen[-1])[0]["error"] == "not every question is answered"

    async def test_the_listings_are_the_catalogue_words_appended_to_the_reply(self) -> None:
        model, _ = _scripted(
            lambda m, i: _call("search_listings"),
            lambda m, i: _final(i, "slot_answer", "Bonne nouvelle !"),
        )
        turn = await TenantSearchAgent(model).reply(
            "4 mois", facts=_FULL, search=DemoCatalogue.yaounde()
        )

        assert [hit.price_xaf for hit in turn.hits] == [60000]
        assert turn.reply.startswith("Bonne nouvelle !\n\n")
        assert "60 000" in turn.reply

    async def test_a_failing_catalogue_is_a_reply_never_a_crash(self) -> None:
        class Broken:
            async def search_for_conversation(
                self, query: Any, *, limit: int = 5
            ) -> list[ConversationPropertyHit]:
                raise ConnectionError("db down")

        model, _ = _scripted(
            lambda m, i: _call("search_listings"),
            lambda m, i: _final(i, "slot_answer", ""),
        )
        turn = await TenantSearchAgent(model).reply("4 mois", facts=_FULL, search=Broken())

        assert turn.hits == ()
        assert turn.reply  # the platform's "try again shortly", not silence


class TestNoInventedAmounts:
    async def test_a_price_nobody_stated_is_sent_back_to_be_rewritten(self) -> None:
        model, seen = _scripted(
            lambda m, i: _final(i, "meta_question", "Votre budget est de 150 000 FCFA."),
            lambda m, i: _final(i, "meta_question", "Votre budget est de 60 000 FCFA."),
        )
        turn = await TenantSearchAgent(model).reply(
            "tu te rappelles de mon budget ?", facts={"rent": 60000}
        )

        assert turn.reply == "Votre budget est de 60 000 FCFA."
        # The second call was shown why the first reply was refused.
        assert len(seen) == 2
        assert "[150000]" in str(seen[1][-1])

    def test_amounts_are_read_the_way_they_are_written_here(self) -> None:
        assert amounts_in("60mil, 60k, 60 000 ou 60.000 FCFA, 4 mois") == {60000}


class TestState:
    async def test_the_model_reads_the_stored_facts_every_turn(self) -> None:
        model, seen = _scripted(
            lambda m, i: _final(i, "meta_question", "Un studio, oui."),
        )
        await TenantSearchAgent(model).reply(
            "je cherchais quoi ?", facts={"property_type": "STUDIO"}
        )

        instructions = seen[0][-1].instructions or ""
        assert "'property_type': 'STUDIO'" in instructions
        assert "Next question" in instructions

    async def test_the_history_is_carried_to_the_next_turn(self) -> None:
        model, seen = _scripted(
            lambda m, i: _final(i, "social", "Bonsoir ! Vous cherchez un logement ?"),
            lambda m, i: _final(i, "business_intent", "Quel type de logement ?"),
        )
        agent = TenantSearchAgent(model)
        first = await agent.reply("Bonsoir", facts={})
        await agent.reply("oui", facts=first.facts, history=first.messages)

        assert len(seen[1]) > len(seen[0])
