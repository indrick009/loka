"""Kora as one agent: the whole thread, the user's memory, each service as tools.

The model reads every message whatever the service, with what the user said
before — in this task and in the earlier ones — and decides what he wants.
What a service does stays in its code: a tool only records the action and
the facts its domain accepted (the food search, the order, the tenant's
answers), and the conversation pipeline runs it as before. The lists a user
reads are the domains' words, never the model's.

Food (client) and the tenant search are its tools so far; every other service
is handed back to the pipeline (``other_service``).
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from typing import Any, Literal

from pydantic import BaseModel, Field
from pydantic_ai import Agent, ModelRetry, RunContext
from pydantic_ai.messages import ModelMessage
from pydantic_ai.models import Model
from pydantic_ai.usage import UsageLimits

from loka.bounded_contexts.ai.infrastructure.agent.tenant_search import (
    SEARCH_FIELDS,
    amounts_in,
    check_lodging,
    missing_fields,
)
from loka.bounded_contexts.conversation.application.ports import KoraState
from loka.bounded_contexts.housing.conversation.facts import FactExtractor
from loka.shared.domain.knowledge.where import place_from

DOUALA = timezone(timedelta(hours=1))
TURN_LIMITS = UsageLimits(request_limit=6)
# Kora's own price, always allowed in a reply.
_KORA_AMOUNTS = frozenset({1000})

_TASKS = {
    "SUPPORT": "none",
    "FOOD_SEARCH": "a client looking for a dish cooking today",
    "FOOD_RESTAURANT": "a restaurateur publishing his menu",
    "PROPERTY_SEARCH": "a tenant looking for a lodging",
    "PROPERTY_CREATION": "a landlord publishing a lodging",
    "AVAILABILITY_CHECK": "a landlord confirming a lodging is free",
    "DATING_PROFILE": "a dating profile",
    "FOUND_DECLARE": "a found document being declared",
    "FOUND_CLAIM": "a lost document being looked for",
    "BOOKING_TRIP": "a bus ticket",
}

# Where the tenant has a list of lodgings in front of him.
_LISTED_STEPS = frozenset({"PRESENT_RESULTS", "AWAITING_MATCH", "COLLECT_PROPERTY_CHOICE"})

_LODGING_GLOSS = {
    "property_type": "the kind of lodging (room, studio, apartment, house)",
    "location": (
        "where he wants to live: his WhatsApp position shared from that zone, or "
        "the neighbourhood(s) and city"
    ),
    "rent": "the maximum monthly rent he accepts, in FCFA",
    "advance_months": "how many months of rent he can pay up front",
}

_INSTRUCTIONS = """\
You are Kora, one WhatsApp assistant in Cameroon for everything: lodging (find \
or publish), food (find a dish cooking today, or publish a restaurant's \
menu), dating, lost and found documents, bus tickets. You hold one \
conversation with the user across all of them: what he said for one service \
is still known for the next, in the memory below.

What you do yourself, with your tools:
- Someone who wants to eat or drink, or asks whether something is served \
("il y a des jus ?"): search_food. Every dish or drink he names goes in \
dishes ("poulet ou poisson" is two); nothing named searches all that cooks today.
- He picks a dish from the numbered list Kora sent ("le 2", "je prends le \
poulet de Mama Ngono"): order_food with its line number.
- A tenant looking for a lodging: search_lodging with every answer the \
message gives. Ask only the four questions, in the order the tool returns: \
kind of lodging, where, maximum monthly rent, months of advance. "60mil", \
"60k" mean 60000 FCFA.
- A place he gave before, which he refers to ("même adresse", "ici", "là où \
je suis", "same place"), or a meal searched without saying where while a \
place is known: at_known_place=true, never retyped.
- Only what a tool accepted is done. A refusal is the user's to fix: ask him.
- A question about a dish or a lodging already listed (its conditions, the \
advance, the visit, "c'est les seuls ?") is yours: answer from the list in \
the thread and the state. What they do not say, the landlord or the \
restaurant decides: say so honestly. Never send the list again.
- Everything else is handled by another part of Kora: a restaurateur and his \
menu, a landlord and his lodging, the pick of a lodging from a list or a \
price offered on it, dating, documents, bus tickets, a payment. Set act to \
other_service and leave reply empty.

How you talk:
- Read what he really says. A greeting gets a greeting, a remark a real \
answer. A message about something already done ("tu as ma commande ?", "je \
reste sur ce que j'ai commandé", "j'ai déjà envoyé") is answered from the \
memory and the task below: call no tool for it.
- At most one question per message, short and warm. Plain WhatsApp text, no \
markdown, at most one emoji. Never repeat a sentence Kora already sent.
- Never invent a dish, a price, a place, a lodging or a person: the lists are \
written by the system, never by you. Never claim you have no memory.
- act: done when a tool did the action, answer when you only reply, \
other_service as above.
"""


class KoraReply(BaseModel):
    act: Literal["done", "answer", "other_service"] = Field(
        description="done: a tool did it; answer: you only reply; other_service: see rules."
    )
    language: Literal["fr", "en"] = Field(
        description="The user's language; fewer than three words keeps the session language."
    )
    reply: str = Field(description="The WhatsApp message Kora sends; empty for other_service.")


@dataclass(slots=True)
class KoraDeps:
    state: KoraState
    user_text: str
    extractor: FactExtractor = field(default_factory=FactExtractor)
    action: str | None = None
    entities: dict[str, Any] = field(default_factory=dict)
    lodging: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True, slots=True)
class KoraTurn:
    act: str
    reply: str
    language: str
    action: str | None
    entities: dict[str, Any]
    input_tokens: int
    output_tokens: int


class KoraAgentModel:
    def __init__(self, model: Model | str, *, retries: int = 2) -> None:
        self._agent: Agent[KoraDeps, KoraReply] = Agent(
            model,
            deps_type=KoraDeps,
            output_type=KoraReply,
            instructions=_INSTRUCTIONS,
            retries=retries,
        )
        self._register(self._agent)

    async def reply(
        self, text: str, *, state: KoraState, history: list[ModelMessage]
    ) -> KoraTurn:
        deps = KoraDeps(state=state, user_text=text)
        if state.flow == "PROPERTY_SEARCH":
            deps.lodging = {k: v for k, v in state.facts.items() if k in SEARCH_FIELDS}
        result = await self._agent.run(
            text, deps=deps, message_history=history, usage_limits=TURN_LIMITS
        )
        usage = result.usage
        return KoraTurn(
            act=result.output.act,
            reply=result.output.reply.strip(),
            language=result.output.language,
            action=deps.action,
            entities=deps.entities,
            input_tokens=usage.input_tokens or 0,
            output_tokens=usage.output_tokens or 0,
        )

    @staticmethod
    def _register(agent: Agent[KoraDeps, KoraReply]) -> None:
        @agent.instructions
        def state(ctx: RunContext[KoraDeps]) -> str:
            return describe(ctx.deps.state)

        @agent.tool
        def search_food(
            ctx: RunContext[KoraDeps],
            dishes: list[str] | None = None,
            city: str | None = None,
            neighbourhood: str | None = None,
            at_known_place: bool = False,
        ) -> dict[str, Any]:
            """Look for what cooks today near the user; the system sends the list.

            Args:
                dishes: every dish he asked for, empty for anything.
                city: the city he named, if any.
                neighbourhood: the district he named, if any.
                at_known_place: the place he gave earlier, in the memory or this task.
            """
            deps = ctx.deps
            place: dict[str, Any] | None = None
            if city or neighbourhood:
                place = place_from({"city": city, "neighbourhood": neighbourhood})
                if place is None:
                    return {"error": "unknown place: ask him the city and the district"}
            elif at_known_place or deps.state.flow == "FOOD_SEARCH":
                place = known_place(deps.state)
            entities: dict[str, Any] = {}
            named = [d.strip() for d in dishes or [] if d.strip()]
            if named:
                entities["dish"] = " ou ".join(named)
            if place:
                entities.update({k: v for k, v in place.items() if v is not None})
            deps.action, deps.entities = "food_search", entities
            if place is None:
                return {
                    "recorded": True,
                    "place": "missing",
                    "advice": "ask where he is: share his WhatsApp location, or city and district",
                }
            return {
                "recorded": True,
                "place": place,
                "advice": "the system sends the list with photos: one short sentence at most",
            }

        @agent.tool
        def order_food(ctx: RunContext[KoraDeps], line: int) -> dict[str, Any]:
            """Pass his order of one dish from the numbered list on to its restaurant.

            Args:
                line: the line number of the dish in the list Kora sent.
            """
            offers = ctx.deps.state.offers
            if not offers:
                return {"error": "no list is open: search_food first, or answer him"}
            if not 1 <= line <= len(offers):
                return {"error": f"the list has lines 1 to {len(offers)}: ask which one"}
            ctx.deps.action, ctx.deps.entities = "food_order", {"choice": line}
            return {
                "ordered": offers[line - 1],
                "advice": "the system passes it on and confirms: one short sentence at most",
            }

        @agent.tool
        def search_lodging(
            ctx: RunContext[KoraDeps],
            property_type: Literal["ROOM", "STUDIO", "APARTMENT", "HOUSE"] | None = None,
            neighbourhoods: list[str] | None = None,
            city: str | None = None,
            at_known_place: bool = False,
            max_rent_xaf: int | None = None,
            advance_months: int | None = None,
        ) -> dict[str, Any]:
            """Store a tenant's answers. Only pass what he gave in this message or before.

            Args:
                property_type: the kind of lodging he wants.
                neighbourhoods: the districts he accepts, the main one first.
                city: the city, when he named one.
                at_known_place: he wants to live at the place he gave earlier.
                max_rent_xaf: the highest monthly rent he accepts, in FCFA.
                advance_months: how many months of rent he can pay up front.
            """
            deps = ctx.deps
            earlier = known_place(deps.state) if at_known_place else None
            accepted, rejected = check_lodging(
                deps.extractor,
                property_type=property_type,
                neighbourhoods=neighbourhoods,
                city=city,
                max_rent_xaf=max_rent_xaf,
                advance_months=advance_months,
                place=earlier,
            )
            if at_known_place and earlier is None:
                rejected["location"] = "no place is known: ask where he wants to live"
            deps.lodging.update(accepted)
            deps.action, deps.entities = "lodging", dict(deps.lodging)
            missing = missing_fields(deps.lodging)
            return {
                "accepted": sorted(accepted),
                "rejected": rejected,
                "next_question": _LODGING_GLOSS[missing[0]] if missing else None,
                "advice": (
                    "ask the next question in your own words"
                    if missing
                    else "the search is recorded and the lodgings with their photos "
                    "follow: say in one short sentence that you are looking, list nothing"
                ),
            }

        @agent.output_validator
        def no_invented_amounts(ctx: RunContext[KoraDeps], output: KoraReply) -> KoraReply:
            invented = amounts_in(output.reply) - allowed_amounts(ctx.deps)
            if invented:
                raise ModelRetry(
                    f"Your reply quotes {sorted(invented)} FCFA, which nobody stated. "
                    "Remove these amounts; never write a price yourself."
                )
            return output


def known_place(state: KoraState) -> dict[str, Any] | None:
    """The place of this task, else the last one the user gave."""
    for source in (state.facts, state.memory):
        for name in ("place", "location"):
            value = source.get(name)
            if isinstance(value, dict) and value.get("city"):
                return dict(value)
    return None


def allowed_amounts(deps: KoraDeps) -> set[int]:
    """Amounts a reply may quote: the user's, the state's, and Kora's price."""
    said = deps.user_text
    allowed = set(_KORA_AMOUNTS) | amounts_in(said)
    allowed.update(int(d) * 1000 for d in re.findall(r"(?<!\d)\d{1,3}(?!\d)", said))
    stated = json.dumps([deps.state.facts, deps.state.offers, deps.state.memory], default=str)
    allowed.update(int(d) for d in re.findall(r"\d{4,}", stated))
    return allowed


def describe(state: KoraState) -> str:
    """The state block the model reads before the message."""
    language = "English" if state.language == "en" else "French"
    lines = [
        f"Today: {datetime.now(DOUALA).strftime('%A %d %B %Y, %H:%M')} (Cameroon).",
        f"Session language: {language}.",
    ]
    tasks = state.memory.get("tasks")
    if isinstance(tasks, list) and tasks:
        lines.append("Memory, the user's earlier tasks (oldest first, times in UTC):")
        lines.extend(
            f"- {_TASKS.get(str(t.get('task')), t.get('task'))}, {t.get('at')}, "
            f"ended at {t.get('ended_at_step')}: {_compact(t.get('facts'))}"
            for t in tasks
            if isinstance(t, dict)
        )
    place = state.memory.get("place")
    if isinstance(place, dict):
        lines.append(f"Last place he gave: {_compact(place)}.")
    lines.append(f"Task under way: {_TASKS.get(state.flow, state.flow)} (step {state.step}).")
    if state.facts:
        lines.append(f"Its facts: {_compact(state.facts)}.")
    if state.open_question:
        lines.append(f"The question Kora left open: {state.open_question}.")
    if state.offers:
        lines.append("The numbered list Kora sent:")
        lines.extend(
            f"{i}. {o.get('dish')} — {o.get('price')} FCFA — {o.get('restaurant')}"
            for i, o in enumerate(state.offers, start=1)
        )
    if state.flow == "PROPERTY_SEARCH":
        missing = missing_fields(state.facts)
        if missing:
            lines.append(f"Lodging question still to ask: {_LODGING_GLOSS[missing[0]]}.")
        elif state.step in _LISTED_STEPS:
            # Journal of 7 Nov 2026, 10:39: « le propriétaire demande 6 mois
            # d'avance » — the tenant's own 6, given to the landlord.
            lines.append(
                "The lodgings Kora listed say only their kind, district, rent and "
                "distance. Their landlord's conditions (advance, deposit, visit, "
                "parking, duration) are unknown to you: the rent and advance in the "
                "facts are the tenant's answers, never the landlord's. Say the "
                "landlord decides them: once the tenant picks a number, Kora asks "
                "the landlord whether it is still free, then the tenant gets his "
                "number for 1 000 FCFA and asks him directly. Never promise that "
                "Kora will ask the landlord anything else."
            )
    return "\n".join(lines)


def _compact(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, default=str)
