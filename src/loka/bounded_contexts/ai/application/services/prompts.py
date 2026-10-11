"""Everything the pipeline says to a model, and nothing it decides.

Extracted from ``analyse_conversation_message``, which had grown to carry the
orchestration, the prompts, the result rendering and the listing confirmation
at once. The split is along the line the defect report drew: this module owns
*how we ask*, the use case owns *what happens next*. A prompt change can now be
read, reviewed and tested without opening the state machine.

Two prompts live here and they share their persona, their price rule and their
topic glosses on purpose: when only one of them carried a name, the assistant
introduced itself as "Loka" on one turn and "Kasa" on another, from an account
called Kora.
"""

from __future__ import annotations

import json
from collections.abc import Mapping
from typing import Any

from loka.bounded_contexts.ai.application.ports import TurnSummary
from loka.bounded_contexts.ai.domain.knowledge import market
from loka.bounded_contexts.ai.domain.services.ai_provider import (
    SUPPORTED_LANGUAGES,
    Prompt,
)
from loka.bounded_contexts.ai.domain.services.fact_extraction import (
    PROPERTY_REQUIREMENTS,
    SEARCH_REQUIREMENTS,
    SUPPORTED_INTENTS,
    question_key_for,
)
from loka.bounded_contexts.messaging.domain.entities.conversation_session import (
    FlowName,
    FlowStep,
)

# Both prompts below can write the sentence a user reads, so the identity lives
# in one place. When only one of them carried a name, the assistant introduced
# itself as "Loka" on one turn and "Kasa" on another, from an account called Kora.
PERSONA = "You are Kora, a warm WhatsApp assistant for a Cameroonian services platform."

# A reference band anchors the model's own sense of magnitude — a studio at "50"
# means 50 000 francs. It is sourced, wide and partly unconfirmed, so it is not a
# tariff: quoted to a user it becomes a commercial promise the platform never
# made. One did reach a tenant as "le loyer minimum pour un appartement est de
# 40 000 FCFA".
PRICE_REFERENCE_RULE = (
    "A price reference range is background for your own judgement, never a "
    "tariff: never quote it to the user as a price, a minimum or a maximum."
)

SYSTEM_PROMPT = (
    f"{PERSONA} "
    "You classify WhatsApp messages for a Cameroonian real-estate platform. "
    "Reply with one JSON object and nothing else. "
    "Never invent a value that is not in the message: use null when unsure. "
    "Amounts are in XAF. Numbers must be plain integers."
    " You are given the state of the conversation (flow, step, the question the "
    "user is answering, the facts already known, the ordered required_fields, and "
    "the typical price_reference_monthly_xaf). Use them: a short reply like a "
    "neighbourhood or a number is an answer to that question, so set the matching "
    "entity instead of asking again. When 'conversation.awaiting_field' is set, a "
    "short bare answer (a number, 'immédiat', a date) belongs to exactly that "
    "entity and to no other."
    " A bare number given as a rent is in FCFA: '50' means 50000, not 50."
    " Set 'intent' to exactly one value from the 'intent' enum in 'schema' and nothing else. "
    "A greeting or small talk is SUPPORT; use UNKNOWN only when no listed intent "
    "applies at all."
    " Also set 'language' to the language the user wrote in ('fr' or 'en'), then "
    "set 'reply': the next WhatsApp message to send, written in that same "
    "language, one to three short sentences of plain text (no markdown, no emoji, "
    "no signature). A message too short to tell the language (a bare number or a "
    "single word) keeps 'default_language'. Acknowledge the message briefly, then "
    "ask for the first field "
    "in 'required_fields' that is not already in known_facts; if none is missing, "
    "confirm the summary. If the message was not understood, ask the user to "
    "rephrase instead of posing a question."
    f" {PRICE_REFERENCE_RULE}"
)

# The closed set the model may choose from. Exposed in the prompt so the model
# never invents a state the platform does not have: an unsupported intent is
# discarded as untrusted, which would turn a simple "bonjour" into a confusing
# rephrase request.
ALLOWED_INTENTS: tuple[str, ...] = tuple(sorted(SUPPORTED_INTENTS))

REPLY_SYSTEM_PROMPT = (
    f"{PERSONA} Write the next message the assistant sends to the user. "
    "Answer in the language given in the input. One to three short sentences, plain "
    "text: no markdown, no emoji, no letter salutation and no signature. "
    "If a question is given, ask that question and no other, naturally in your own "
    "words; never substitute a different one, and never ask for something already "
    "present in known_facts. Do not answer that question on the user's behalf — "
    "only the user can say what their answer is. "
    "Never invent a listing, a price or a fact that is not in the context, and "
    f"never promise anything the platform did not confirm. {PRICE_REFERENCE_RULE}"
)

# A short, provider-neutral gloss for each question, so the reply model knows
# what it is asking for without reading the outbound layer's copy.
QUESTION_TOPICS: dict[str, str] = {
    "ask.property_type": "the type of property (apartment, studio, house, ...)",
    "ask.standing": "whether the property is modern or not",
    "ask.location": "the city and neighbourhood",
    "ask.rent": "the monthly rent, in XAF",
    "ask.features": "the number of bedrooms and bathrooms, and the surface in m2",
    "ask.charges": "whether charges are included and, if not, their monthly amount",
    "ask.deposit": "the refundable deposit (caution) the tenant must pay, in XAF",
    "ask.minimum_duration": "the minimum rental duration",
    "ask.availability": "when the property becomes available (a date, or immediately)",
    "ask.conditions": (
        "the entry conditions: the advance in months, the agency or lease fee, "
        "and the tenant type accepted"
    ),
    "ask.confirm_property": "confirmation of the summary before publishing",
    "confirm.published": "confirmation that the property has been published",
    # The tenant's side: a search asks its own questions, and these glosses are
    # what the reply model reads instead of the outbound copy.
    "ask.search_type": "the type of property the tenant is looking for",
    "ask.search_location": "the city and neighbourhood the tenant wants",
    "ask.search_budget": "the maximum monthly rent the tenant is willing to pay, in XAF",
    "ask.search_criteria": "which search criterion the tenant wants to change",
    "ask.search_results": "the list of properties matching the tenant's criteria",
    "ask.search_choice": "which property from the list the tenant picks",
}

# What a month of rent costs here, by property type, in XAF. A model with no
# Cameroonian grounding reads a bare "50" as fifty francs; these wide bands anchor
# the magnitude only — they never reject a price the user is sure about. The
# values live in the sourced market knowledge base so the code and the prompt
# share one authority (see ``ai.domain.knowledge.market``).
PRICE_REFERENCES_XAF: dict[str, tuple[int, int]] = {
    name: (band.low_xaf, band.high_xaf) for name, band in market.PRICE_BANDS_XAF.items()
}

# The same question is asked from opposite sides: a landlord lists a property, a
# tenant looks for one. The neutral gloss lets the model default to the tenant
# reading ("le loyer que vous recherchez") even in a listing, which reads as if
# the platform had mixed the two roles up. These overlays fix the wording.
_LANDLORD_TOPICS: dict[str, str] = {
    "ask.property_type": "the type of property the landlord is listing",
    "ask.standing": "whether the property the landlord is listing is modern or not",
    "ask.location": "the city and neighbourhood where the property is",
    "ask.rent": "the monthly rent the landlord is asking, in XAF",
    "ask.charges": (
        "whether the charges on the landlord's property are included in the "
        "rent and, if not, their monthly amount"
    ),
    "ask.deposit": (
        "the refundable deposit (caution) the landlord asks of the tenant, in XAF"
    ),
    "ask.minimum_duration": "the minimum duration the landlord wants for the rental",
    "ask.availability": "when the landlord's property becomes available (a date, or immediately)",
    "ask.conditions": (
        "the landlord's entry conditions: the advance in months, the agency or "
        "lease fee, and the tenant type the landlord accepts"
    ),
}
_TENANT_TOPICS: dict[str, str] = {
    "ask.property_type": "the type of property the tenant is looking for",
    "ask.standing": "whether the tenant wants a modern property or not",
    "ask.location": "the city and neighbourhood the tenant wants",
    "ask.rent": "the maximum monthly rent the tenant is willing to pay, in XAF",
}
_FLOW_TOPICS: dict[FlowName, dict[str, str]] = {
    FlowName.PROPERTY_CREATION: _LANDLORD_TOPICS,
    FlowName.PROPERTY_SEARCH: _TENANT_TOPICS,
}
_FLOW_ROLES: dict[FlowName, str] = {
    FlowName.PROPERTY_CREATION: "a landlord listing a property for rent",
    FlowName.PROPERTY_SEARCH: "a tenant looking for a property to rent",
    FlowName.SUPPORT: "a user who needs help with the platform",
}


def question_topic(next_key: str | None, flow: FlowName) -> str | None:
    """The plain-words gloss of the next question, phrased for this flow."""
    if not next_key:
        return None
    overlay = _FLOW_TOPICS.get(flow, {})
    return overlay.get(next_key) or QUESTION_TOPICS.get(next_key)


# The schema entity a short answer to each question fills. Handing the model the
# target field, not only the question key, is what stops a bare "5" from being
# stored as availability instead of the minimum duration the platform asked for.
_FIELD_FOR_QUESTION: dict[str, str] = {
    "ask.property_type": "property_type",
    "ask.standing": "standing",
    "ask.location": "city and neighbourhood",
    "ask.rent": "price",
    "ask.features": "bedrooms, bathrooms, surface_area",
    "ask.charges": "charges and charging_policy",
    "ask.deposit": "deposit",
    "ask.minimum_duration": "minimum_duration_months",
    "ask.availability": "availability",
    "ask.conditions": "conditions",
    # The search questions bind to the same entities: a tenant answering a
    # budget question with "150" is giving a price, exactly as a landlord is.
    "ask.search_type": "property_type",
    "ask.search_location": "city and neighbourhood",
    "ask.search_budget": "price",
}

RESPONSE_SCHEMA: dict[str, Any] = {
    "type": "object",
    "required": ["intent", "confidence", "entities"],
    "properties": {
        "intent": {"type": "string", "enum": list(ALLOWED_INTENTS)},
        "confidence": {"type": "number", "minimum": 0, "maximum": 1},
        "language": {"type": "string"},
        "reply": {"type": "string"},
        "entities": {
            "type": "object",
            "properties": {
                "property_type": {"type": "string"},
                "standing": {"type": "string", "enum": ["MODERN", "NON_MODERN"]},
                "city": {"type": "string"},
                "neighbourhood": {"type": "string"},
                "price": {"type": "integer"},
                "bedrooms": {"type": "integer"},
                "bathrooms": {"type": "integer"},
                "surface_area": {"type": "integer"},
                "charges": {"type": "integer"},
                "charging_policy": {"type": "string", "enum": ["INCLUDED", "EXTRA"]},
                # The mission wants eau and électricité stated separately when
                # the landlord gives both; the code sums them for ``charges``.
                "water_charges": {"type": "integer"},
                "electricity_charges": {"type": "integer"},
                "deposit": {"type": "integer"},
                "minimum_duration_months": {"type": "integer"},
                "availability": {"type": "string"},
                "conditions": {"type": "string"},
                # Anything the landlord *said* about these five axes. Hints are
                # the probable readings, which never become facts on a listing.
                "amenities": {
                    "type": "object",
                    "properties": {
                        "parking": {"type": "boolean"},
                        "water": {"type": "boolean"},
                        "electricity": {"type": "boolean"},
                        "internet": {"type": "boolean"},
                        "security": {"type": "boolean"},
                        "extras": {"type": "array", "items": {"type": "string"}},
                    },
                },
                "amenities_hints": {
                    "type": "object",
                    "properties": {
                        "parking": {"type": "boolean"},
                        "water": {"type": "boolean"},
                        "electricity": {"type": "boolean"},
                        "internet": {"type": "boolean"},
                        "security": {"type": "boolean"},
                    },
                },
            },
        },
    },
}


def collapse_repeats(text: str) -> str:
    """Drop a phrase the model emitted several times in a row.

    A reply model occasionally loops on a short fragment ("en sus en sus en sus
    en plus du loyer"). The loop is a decoding artefact, not meaning, and sending
    it verbatim makes the assistant look broken. Runs of a repeated one- or
    two-word unit collapse to a single occurrence; numeric tokens are left alone
    so a price never loses a digit.
    """
    tokens = text.split()
    out: list[str] = []
    index = 0
    total = len(tokens)
    while index < total:
        for unit in (2, 1):
            if index + 2 * unit > total:
                continue
            fragment = [token.casefold() for token in tokens[index : index + unit]]
            if not any(any(char.isalpha() for char in token) for token in fragment):
                continue
            end = index + unit
            while (
                end + unit <= total
                and [token.casefold() for token in tokens[end : end + unit]] == fragment
            ):
                end += unit
            if end > index + unit:
                out.extend(tokens[index : index + unit])
                index = end
                break
        else:
            out.append(tokens[index])
            index += 1
    return " ".join(out)


def build_analysis_prompt(
    *,
    text: str | None,
    default_language: str,
    history: list[TurnSummary],
    flow: FlowName,
    step: FlowStep,
    pending_question: str | None,
    known_facts: dict[str, Any],
) -> Prompt:
    """Assemble the request, with the state of the conversation as context.

    The context is what turns "Bastos" from noise into an answer: the model can
    see that it is replying to the location question of an open listing, and
    fill the matching entity instead of guessing an intent from three words.

    The phone number is never part of the prompt: the model has no business
    knowing who it is talking to, and prompts end up in vendor logs.
    """
    public_known = _public_known_facts(known_facts)
    payload: dict[str, Any] = {
        "message": text or "",
        # The language of the *message* is the model's to read; this is only the
        # stand-in it keeps when the message is too short to tell.
        "default_language": (
            default_language if default_language in SUPPORTED_LANGUAGES else "fr"
        ),
        "you_are_helping": _FLOW_ROLES.get(flow, _FLOW_ROLES[FlowName.SUPPORT]),
        "conversation": {
            "flow": flow.value,
            "step": step.value,
            "awaiting_answer_to": pending_question,
            "awaiting_question": question_topic(pending_question, flow),
            "awaiting_field": _FIELD_FOR_QUESTION.get(pending_question or ""),
            "known_facts": public_known,
        },
        # Only what is still missing, in order, so the model asks the next real
        # question without re-reading fields already collected. The schema closes
        # the intent enum, so a second copy of the allowlist would be dead bytes.
        "required_fields": _missing_fields(flow, public_known),
        "schema": RESPONSE_SCHEMA,
    }
    # The price band anchors the magnitude of a bare number; once the rent is
    # known it is context that can never be used again, so it is dropped.
    if not public_known.get("rent"):
        payload["price_reference_monthly_xaf"] = {
            name: {"min": low, "max": high}
            for name, (low, high) in PRICE_REFERENCES_XAF.items()
        }
    turns = _turns_as_json(history)
    if turns:
        payload["recent_turns"] = turns
    return Prompt(
        system=SYSTEM_PROMPT,
        user=json.dumps(payload, ensure_ascii=False),
        response_schema=RESPONSE_SCHEMA,
    )


# Keys the pipeline keeps in the session for its own use, never for the model:
# leaking "next_question" into known_facts invites the model to treat an internal
# variable as a fact about the property.
_PROMPT_INTERNAL_KEYS = frozenset({"next_question", "abort_reason"})


def _public_known_facts(known_facts: dict[str, Any]) -> dict[str, Any]:
    return {
        name: value
        for name, value in known_facts.items()
        if name not in _PROMPT_INTERNAL_KEYS and not str(name).startswith("_")
    }


def _missing_fields(flow: FlowName, known: dict[str, Any]) -> list[dict[str, str]]:
    """The still-missing form fields, in order, glossed for this flow.

    Making the model decide the next question is expensive in tokens *and*
    fragile; handing it only the remaining fields lets it do both jobs (read the
    message, ask the next question) while the code still owns the order. Each
    flow walks its own form: a tenant is never shown the landlord's charges and
    deposit as fields still to come.
    """
    requirements = (
        SEARCH_REQUIREMENTS if flow is FlowName.PROPERTY_SEARCH else PROPERTY_REQUIREMENTS
    )
    fields: list[dict[str, str]] = []
    for name, step in requirements:
        if name in known:
            continue
        fields.append(
            {"field": name, "ask": question_topic(question_key_for(step, flow), flow) or ""}
        )
    return fields


def build_reply_prompt(
    *,
    text: str,
    language: str,
    intent: str,
    accepted: bool,
    accepted_facts: Mapping[str, Any],
    next_key: str | None,
    history: list[TurnSummary],
    flow: FlowName,
    known_facts: dict[str, Any],
) -> Prompt:
    """Ask the model to write the sentence this turn sends back.

    Only accepted facts are shown, and the next question is described in plain
    words rather than by its internal key: the model tailors the phrasing, it
    does not get to decide what is asked — the code already did. The flow is
    passed too, so the wording matches the side of the conversation: a landlord
    is asked the rent they *want*, a tenant the rent they *seek*.

    The turn's outcome arrives as three plain values rather than as the use
    case's verdict object: taking the object would make this module import the
    state machine that imports it, and a prompt builder has no business
    knowing that type.
    """
    return Prompt(
        system=REPLY_SYSTEM_PROMPT,
        user=json.dumps(
            {
                "message": text,
                "language": language if language in SUPPORTED_LANGUAGES else "fr",
                "you_are_helping": _FLOW_ROLES.get(flow, _FLOW_ROLES[FlowName.SUPPORT]),
                "understood": {
                    "intent": intent,
                    "accepted": accepted,
                    # Everything the session holds, not only what this message
                    # produced. A writer given one turn's extraction truthfully
                    # reports having no memory, then invents a number to fill
                    # the gap — and it is also how it asks for a field that was
                    # answered three turns ago. This turn's facts win, because a
                    # correction is the one case where they disagree.
                    "known_facts": {
                        **_public_known_facts(known_facts),
                        **(dict(accepted_facts) if accepted else {}),
                    },
                },
                # None means "the message was not understood": ask for a rephrase
                # rather than posing a question the platform never reached.
                "ask_about": question_topic(next_key, flow),
                "recent_turns": _turns_as_json(history),
            },
            ensure_ascii=False,
        ),
        temperature=0.4,
        max_output_tokens=256,
    )


def _turns_as_json(history: list[TurnSummary]) -> list[dict[str, Any]]:
    return [
        {
            "direction": turn.direction,
            "intent": turn.intent,
            "confidence": round(turn.confidence, 2),
            "used_llm": turn.used_llm,
        }
        for turn in history
    ]
