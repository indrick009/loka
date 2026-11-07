"""A tenant search led by the model, with the business rules as tools.

The prototype behind the question "does a modern model, given tools, hold a
better conversation than the classifier and the templates?". The split of
authority is the one ``docs/conversational-engine.md`` sets, moved, not
loosened:

- **The model** reads the message, decides what the user is doing (an answer,
  a correction, a question, a greeting…) and writes the reply in its own words.
- **The code** decides which facts are valid (``FactExtractor``, the same
  value objects as the pipeline), which question is still open
  (``SEARCH_REQUIREMENTS``), and what the catalogue holds (the search port).
  The listings a tenant reads are formatted by ``results_text``, never by the
  model, and a reply quoting an amount nobody stated is sent back to be
  rewritten.

So the model may only *propose* a fact through ``record_facts``; a refused
value is reported back to it and never stored. It cannot search before the
four questions are answered, and it cannot invent a listing because it never
writes one.
"""

from __future__ import annotations

import re
from collections.abc import Sequence
from dataclasses import dataclass, field
from typing import Any, Literal

from pydantic import BaseModel, Field
from pydantic_ai import Agent, ModelRetry, RunContext
from pydantic_ai.messages import ModelMessage
from pydantic_ai.models import Model

from loka.bounded_contexts.conversation.application.ports import (
    ConversationPropertyHit,
    PropertySearchGateway,
)
from loka.bounded_contexts.housing.conversation.facts import (
    SEARCH_REQUIREMENTS,
    FactExtractor,
)
from loka.bounded_contexts.housing.conversation.texts.conversation_copy import (
    no_results_text,
    results_text,
    search_query,
    search_unavailable_text,
)
from loka.shared.domain.knowledge import market

# What the tenant is asked, glossed for the model — the four questions of
# Housing §6 and nothing else.
_FIELD_GLOSS: dict[str, str] = {
    "property_type": "the kind of lodging (room, studio, apartment, house)",
    "location": (
        "where he wants to live: offer first to share his WhatsApp position "
        "while standing in that zone (workplace or target district, not his "
        "current home); otherwise the neighbourhood(s) and city"
    ),
    "rent": "the maximum monthly rent he accepts, in FCFA",
    "advance_months": "how many months of rent he can pay up front",
}

SEARCH_FIELDS: tuple[str, ...] = tuple(name for name, _ in SEARCH_REQUIREMENTS)

# The 1 000 FCFA unlock may always be named; it is Kora's own price.
_ALWAYS_ALLOWED_AMOUNTS = frozenset({1000})
_HISTORY_LIMIT = 30

DialogueAct = Literal[
    "social",
    "business_intent",
    "slot_answer",
    "correction",
    "topic_switch",
    "meta_question",
    "open_request",
    "confirmation",
    "refusal",
    "unclear",
    "out_of_scope",
    "other_service",
    "listing_choice",
]


class AgentReply(BaseModel):
    """What the model hands back at the end of a turn."""

    act: DialogueAct = Field(description="What the user did with this message.")
    language: Literal["fr", "en"] = Field(
        description=(
            "The language the user writes in. A message of fewer than three "
            "words keeps the session language."
        )
    )
    reply: str = Field(
        description=(
            "The WhatsApp message Kora sends, in the session language. Short, "
            "warm, one question at most. When search_listings returned results, "
            "at most one short sentence: the list itself is appended by the system."
        )
    )


@dataclass(slots=True)
class TenantSearchDeps:
    """The state one turn works on. ``facts`` is mutated by the tools."""

    facts: dict[str, Any]
    language: str = "fr"
    user_text: str = ""
    search: PropertySearchGateway | None = None
    extractor: FactExtractor = field(default_factory=FactExtractor)
    hits: list[ConversationPropertyHit] = field(default_factory=list)
    searched: bool = False
    search_failed: bool = False
    # In the WhatsApp pipeline the search is recorded, not run here: the list,
    # with its photos, follows through the demand matching.
    submit: bool = False
    submitted: bool = False


@dataclass(frozen=True, slots=True)
class AgentTurn:
    """One answered message: what to send, and the state to keep."""

    act: str
    reply: str
    facts: dict[str, Any]
    hits: tuple[ConversationPropertyHit, ...]
    messages: list[ModelMessage]
    language: str = "fr"
    submitted: bool = False
    input_tokens: int = 0
    output_tokens: int = 0


def missing_fields(facts: dict[str, Any]) -> list[str]:
    """The tenant's questions still unanswered, in the order the code sets."""
    return [name for name in SEARCH_FIELDS if facts.get(name) in (None, "", {}, [])]


def describe_facts(facts: dict[str, Any]) -> dict[str, Any]:
    """The stored facts as the model reads them — no internal keys."""
    return {
        name: value
        for name, value in facts.items()
        if not name.startswith("_") and name != "next_question"
    }


_INSTRUCTIONS = """\
You are Kora, a WhatsApp assistant in Cameroon. Here you help a tenant find a \
lodging. Kora connects tenants and landlords directly: once the landlord \
confirms the lodging is still available, the tenant pays 1 000 FCFA to receive \
the landlord's number and the exact location. No agency fee, no visit fee.

How you work:
- Read what the user really says. Greetings get a warm answer, thanks get \
thanks, a question about what he said earlier is answered from the stored \
facts below, a change of mind ("finalement 60mil") replaces the old value.
- Every fact you understand goes through the record_facts tool, all of them \
at once when one message gives several. Only facts the tool accepted are \
known. If it refuses one, ask again for that one in other words.
- Local money: "60mil", "60k", "60 000" all mean 60000 FCFA. Months of advance \
are a number of months, not francs.
- Ask only what is still missing, one question per message, in the order the \
tool returns (next_field). Never ask anything else of a tenant: not the \
standing, the deposit, the charges, the duration.
- When nothing is missing, call search_listings. Never describe, number or \
price a listing yourself: the system appends the real list.
- Never invent a listing, a price, a neighbourhood or a fact the user did not \
give. Never claim to have no memory: the stored facts are your memory.
- Out of scope (football, politics, code…): decline in one sentence and come \
back to the search.
- Kora also does food, dating, lost documents and bus tickets. When the user \
asks for one of those, set act to other_service and leave reply empty: \
another part of Kora answers.
- When the user picks a listing already sent to him ("le 2", "je prends le \
premier") or asks for a lower price on one, set act to listing_choice and \
leave reply empty: Kora asks the landlord, a price offer included.
- Kora sends the photos of each listing with the list: never say it cannot.
- Plain text, WhatsApp style, no markdown headings, at most one emoji.
"""


class TenantSearchAgent:
    """A tenant search conversation driven by a Pydantic AI agent."""

    def __init__(self, model: Model | str, *, retries: int = 2) -> None:
        self._agent: Agent[TenantSearchDeps, AgentReply] = Agent(
            model,
            deps_type=TenantSearchDeps,
            output_type=AgentReply,
            instructions=_INSTRUCTIONS,
            retries=retries,
        )
        self._register(self._agent)

    @property
    def agent(self) -> Agent[TenantSearchDeps, AgentReply]:
        return self._agent

    async def reply(
        self,
        text: str,
        *,
        facts: dict[str, Any],
        history: Sequence[ModelMessage] = (),
        language: str = "fr",
        search: PropertySearchGateway | None = None,
        submit: bool = False,
    ) -> AgentTurn:
        deps = TenantSearchDeps(
            facts=dict(facts), language=language, user_text=text, search=search, submit=submit
        )
        result = await self._agent.run(text, deps=deps, message_history=list(history))
        output = result.output
        message = output.reply.strip()
        if deps.hits:
            # The list is the catalogue's words, never the model's.
            listing = results_text(deps.hits, language)
            message = f"{message}\n\n{listing}" if message else listing
        elif deps.searched and deps.search_failed:
            message = search_unavailable_text(language)
        elif deps.searched and not deps.submitted and not message:
            message = no_results_text(language)
        usage = result.usage()
        return AgentTurn(
            act=output.act,
            reply=message,
            facts=deps.facts,
            hits=tuple(deps.hits),
            messages=result.all_messages()[-_HISTORY_LIMIT:],
            language=output.language,
            submitted=deps.submitted,
            input_tokens=usage.input_tokens or 0,
            output_tokens=usage.output_tokens or 0,
        )

    @staticmethod
    def _register(agent: Agent[TenantSearchDeps, AgentReply]) -> None:
        @agent.instructions
        def state(ctx: RunContext[TenantSearchDeps]) -> str:
            missing = missing_fields(ctx.deps.facts)
            language = "English" if ctx.deps.language == "en" else "French"
            lines = [
                f"Session language: {language}. Answer in it even if one word "
                "of the message is in another language.",
                f"Stored facts: {describe_facts(ctx.deps.facts) or 'none yet'}.",
            ]
            if missing:
                lines.append(
                    f"Next question to ask if the message does not answer it: "
                    f"{_FIELD_GLOSS[missing[0]]}."
                )
            else:
                lines.append("Every question is answered: search_listings may run.")
            return "\n".join(lines)

        @agent.tool
        def record_facts(
            ctx: RunContext[TenantSearchDeps],
            property_type: Literal["ROOM", "STUDIO", "APARTMENT", "HOUSE"] | None = None,
            neighbourhoods: list[str] | None = None,
            city: str | None = None,
            max_rent_xaf: int | None = None,
            advance_months: int | None = None,
        ) -> dict[str, Any]:
            """Store what the tenant said. Only pass fields he actually gave.

            Args:
                property_type: the kind of lodging he wants.
                neighbourhoods: the districts he accepts, the main one first.
                city: the city, when he named one.
                max_rent_xaf: the highest monthly rent he accepts, in FCFA.
                advance_months: how many months of rent he can pay up front.
            """
            entities: dict[str, Any] = {}
            if property_type:
                entities["property_type"] = property_type
            if neighbourhoods:
                entities["neighbourhood"] = neighbourhoods[0]
                entities["neighbourhoods"] = list(neighbourhoods)
            if city:
                entities["city"] = city
            if max_rent_xaf is not None:
                entities["price"] = max_rent_xaf
            if advance_months is not None:
                entities["advance_months"] = advance_months

            checked = ctx.deps.extractor.validate(entities)
            rejected = dict(checked.rejected)
            if "location" in rejected and neighbourhoods and not city:
                # "Zzzville" alone: a hint the code cannot place. Never stored
                # as a city — ask the city instead.
                rejected["location"] = (
                    f"unknown neighbourhood {neighbourhoods[0]!r}: ask which city"
                )
            accepted = {
                name: value for name, value in checked.facts.items() if name in SEARCH_FIELDS
            }
            rent = accepted.get("rent")
            if isinstance(rent, int) and rent < market.DEFAULT_PLAUSIBILITY_WINDOW.min_xaf:
                # "50" is not fifty francs. Asked back rather than multiplied:
                # a guessed thousand is a budget nobody stated.
                del accepted["rent"]
                rejected["rent"] = (
                    f"{rent} FCFA is not a monthly rent: ask whether he means {rent * 1000} FCFA"
                )
            ctx.deps.facts.update(accepted)
            missing = missing_fields(ctx.deps.facts)
            return {
                "accepted": accepted,
                "rejected": rejected,
                "still_missing": missing,
                "next_field": _FIELD_GLOSS[missing[0]] if missing else None,
            }

        @agent.tool
        async def search_listings(ctx: RunContext[TenantSearchDeps]) -> dict[str, Any]:
            """Search the catalogue with the stored facts. Only once nothing is missing."""
            missing = missing_fields(ctx.deps.facts)
            if missing:
                return {"error": "not every question is answered", "still_missing": missing}
            ctx.deps.searched = True
            if ctx.deps.submit:
                ctx.deps.submitted = True
                return {
                    "submitted": True,
                    "advice": (
                        "the search is recorded and the matching lodgings, with "
                        "their photos, follow in the next message: say in one "
                        "short sentence that you are looking, list nothing"
                    ),
                }
            query = search_query(ctx.deps.facts)
            if query is None or ctx.deps.search is None:
                ctx.deps.search_failed = True
                return {"error": "search unavailable: tell the user to try again shortly"}
            try:
                hits = await ctx.deps.search.search_for_conversation(query)
            except Exception:
                ctx.deps.search_failed = True
                return {"error": "search unavailable: tell the user to try again shortly"}
            ctx.deps.hits = list(hits)
            if not hits:
                return {
                    "found": 0,
                    "advice": (
                        "nothing matches: say so plainly and suggest widening one "
                        "criterion (another neighbourhood, a higher rent)"
                    ),
                }
            return {
                "found": len(hits),
                "advice": (
                    "the system appends the numbered list; write at most one "
                    "short sentence and do not repeat the listings"
                ),
            }

        @agent.output_validator
        def no_invented_amounts(
            ctx: RunContext[TenantSearchDeps], output: AgentReply
        ) -> AgentReply:
            invented = invented_amounts(output.reply, ctx.deps)
            if invented:
                raise ModelRetry(
                    f"Your reply quotes {sorted(invented)} FCFA, which nobody "
                    "stated. Remove these amounts; never write a price yourself."
                )
            return output


# "60 000", "60.000", "60000", "60mil", "60 k"
_AMOUNT = re.compile(
    r"(?<![\d])(\d{1,3}(?:[ .\u00a0\u202f]\d{3})+|\d+)\s*(mil(?:le)?s?|k)?\b",
    re.IGNORECASE,
)


def amounts_in(text: str) -> set[int]:
    """Every franc amount a sentence states (small numbers are not amounts)."""
    found: set[int] = set()
    for digits, unit in _AMOUNT.findall(text):
        value = int(re.sub(r"\D", "", digits))
        if unit:
            value *= 1000
        if value >= 1000:
            found.add(value)
    return found


def invented_amounts(text: str, deps: TenantSearchDeps) -> set[int]:
    """Amounts in a reply that neither the user nor the catalogue gave."""
    allowed = set(_ALWAYS_ALLOWED_AMOUNTS) | amounts_in(deps.user_text)
    # "50" said for a rent may be read back as "50 000 ?": that is a question
    # about his number, not an invented one.
    allowed.update(
        int(digits) * 1000 for digits in re.findall(r"(?<!\d)\d{1,3}(?!\d)", deps.user_text)
    )
    rent = deps.facts.get("rent")
    if isinstance(rent, int):
        allowed.add(rent)
    allowed.update(hit.price_xaf for hit in deps.hits)
    return amounts_in(text) - allowed
