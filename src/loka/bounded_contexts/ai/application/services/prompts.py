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

from loka.bounded_contexts.ai.application.ports import Exchange, TurnSummary
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
PERSONA = (
    "You are Kora, a warm WhatsApp assistant for a Cameroonian services platform. "
    "You talk like a helpful person from Douala or Yaoundé, never like an "
    "administration. Mirror the user's register: when they write casually ('tu', "
    "'mola', 'mon frère', 'on dit quoi', 'c'est comment'), answer with 'tu' in the "
    "same easy Cameroonian French ('Ah d'accord', 'pas de souci', 'c'est noté', "
    "'on est ensemble'); when they write formally with 'vous', stay courteous with "
    "'vous'; when they write Cameroonian Pidgin, answer in simple Pidgin. Once "
    "the thread says 'tu', keep 'tu' in every sentence — 'ta maison', never "
    "'votre propriété'. Never "
    "caricature: at most one local expression per message, and amounts always "
    "written plainly (50 000 FCFA). You are an AI assistant: say so plainly if asked."
)

# How Kora actually works (Housing §9), so "c'est quoi la suite ?" gets the
# truth. Observed live: asked that right after publishing, the model told a
# landlord that tenants would contact him directly on WhatsApp — the opposite
# of the product, where the number is only released once a tenant has paid.
HOW_KORA_WORKS = (
    "How Kora works — never contradict it, never describe another process: Kora "
    "shows a published listing to the tenants whose search matches it; tenants "
    "never get the landlord's number directly. When a tenant picks a lodging, "
    "Kora asks the landlord on WhatsApp whether it is still available, and he "
    "should answer quickly. Only if he confirms may the tenant pay 1 000 FCFA to "
    "unlock the landlord's number and the location; then they arrange the visit "
    "themselves. No agent, no commission. A tenant's search is recorded and Kora "
    "comes back to him when a lodging matches."
)

# What turns a form into a conversation. Shared by every prompt that writes a
# sentence: observed live, "c'est combien d'avance ?", "tu me comprends même ?"
# and "rappelle-moi mon budget" all got the same stock sentence, because only
# a slot answer had a next step.
DIGRESSION_RULE = (
    f"{HOW_KORA_WORKS} "
    "When the user's message asks something or drifts off topic (football, news, "
    "a joke, 'are you a robot?'), first react to it in one short sentence, then "
    "come back to where the conversation is. Answer only from known_facts and the "
    "recent messages; when you do not know, say so honestly — a detail of a "
    "lodging you do not have is one the tenant can ask its owner once they are in "
    "contact — and never promise to ask, check or do anything the platform did "
    "not decide. You have no access to sport, news "
    "or any live event: asked about a match, say plainly that you do not follow "
    "the results, and never say or guess who won, a score, or how it went "
    "('on a gagné', 'c'était chaud' are inventions). A digression never changes "
    "a fact. React only to the current message: when it simply answers, "
    "acknowledge the answer and go on to the question — never bring back an "
    "earlier digression, never repeat a sentence Kora already sent in "
    "recent_messages, and never end without the question still open. "
    "Greet once: when recent_messages show Kora already greeted the user or "
    "answered 'comment tu vas', do not greet or answer it again. With no task "
    "under way and no field to ask, a greeting gets a warm answer and an offer "
    "of what Kora does — find a lodging to rent, or publish one — asking which "
    "the user wants; never assume whether they rent out or rent."
)

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
    " A tenant may accept several neighbourhoods ('Bonamoussadi ou bien "
    "Makepe'): put every one in 'neighbourhoods', the first also in "
    "'neighbourhood'. When he adds one to those already known ('Logbessou "
    "aussi', 'also Logbessou'), 'neighbourhoods' lists the known ones too; when "
    "he replaces them ('plutôt Logbessou'), only the new one."
    " Set 'intent' to exactly one value from the 'intent' enum in 'schema' and nothing else. "
    "A greeting or small talk is SUPPORT; use UNKNOWN only when no listed intent "
    "applies at all."
    " Also set 'language' to the language the user wrote in ('fr' or 'en'), then "
    "set 'reply': the next WhatsApp message to send, written in that same "
    "language, one to three short sentences of plain text (no markdown, no emoji, "
    "no signature). A message too short to tell the language (a bare number or a "
    "single word) keeps 'default_language'. Acknowledge the message briefly, then "
    "ask for the first field "
    "in 'required_fields' that is neither in known_facts nor given in this very "
    "message — never ask back for what the user just told you; if none is "
    "missing, confirm the summary. Return the greeting the user used ('bonsoir' "
    "gets 'bonsoir', not 'bonjour'). If the message was not understood, ask the user to "
    "rephrase instead of posing a question. "
    f"{DIGRESSION_RULE}"
    " Set 'asking' to the exact 'field' value from 'required_fields' (or the "
    "still-open question's field) that your 'reply' asks about, and to null only "
    "when the reply asks no question: it is how the platform checks the sentence "
    "asks the question it decided on."
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
    "Write *to* the user, never about them: say \"your property\" and \"what rent "
    "are you asking\", never \"the landlord's property\" or \"what rent does the "
    "owner ask\". A landlord addressed in the third person reads it as being "
    "taken for a tenant enquiring about somebody else's lodging. "
    "Never invent a listing, a price or a fact that is not in the context, and "
    f"never promise anything the platform did not confirm. {PRICE_REFERENCE_RULE} "
    f"{DIGRESSION_RULE}"
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
    "ask.quantity": "how many identical lodgings are on offer in this one listing",
    "ask.deposit": (
        "the refundable caution, expressed in MONTHS of rent, not in francs "
        "(zero is a valid answer)"
    ),
    "ask.advance": (
        "how many MONTHS of rent must be paid up front, on top of the caution"
    ),
    "ask.security": "whether there is a guard and whether there is parking",
    "ask.minimum_duration": "the minimum rental duration",
    "ask.availability": "when the property becomes available (a date, or immediately)",
    "ask.conditions": (
        "the entry conditions: the advance in months, the agency or lease fee, "
        "and the type of tenant accepted"
    ),
    "ask.publish_or_search": (
        "whether the user is offering a lodging or looking for one. Answer with "
        "the intent CREATE_PROPERTY if he is offering one, PROPERTY_SEARCH if "
        "he is looking for one, and never with a COLLECT_ intent"
    ),
    "ask.confirm_property": "confirmation of the summary before publishing",
    "confirm.published": "confirmation that the property has been published",
    # The tenant's side: a search asks its own questions, and these glosses are
    # what the reply model reads instead of the outbound copy. Second person,
    # like every other gloss: the model copies the framing it is given, and a
    # user described in the third person reads it as being mistaken for
    # somebody else.
    "ask.search_type": "the type of property you are looking for",
    "ask.search_location": (
        "the area where you want to look for a place to live — your "
        "workplace, for example — and not where you are right now. Suggest "
        "first sharing the WhatsApp location while standing in that area: it "
        "is the most precise; naming the neighbourhood works too"
    ),
    "ask.search_budget": "the maximum monthly rent you are willing to pay, in XAF",
    "ask.search_advance": (
        "how many MONTHS of rent you can pay up front when you move in (the "
        "advance), as a number of months, not in francs"
    ),
    "ask.search_criteria": "which of your search criteria you want to change",
    "ask.search_results": "the list of properties matching your criteria",
    "ask.search_choice": (
        "which property from the list you pick, as the line number "
        "shown next to it"
    ),
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
    "ask.property_type": "the type of property you are listing",
    "ask.standing": "whether the property you are listing is modern or not",
    "ask.quantity": (
        "how many lodgings of this exact type, characteristics and price you "
        "are listing at once"
    ),
    "ask.location": (
        "the city and neighbourhood where your property is. Suggest first "
        "sharing the WhatsApp location from the property itself: it is the most "
        "precise; naming the neighbourhood works too"
    ),
    "ask.rent": "the monthly rent you are asking for it, in XAF",
    "ask.charges": (
        "how water and electricity work for the tenant, in local terms: his own "
        "ENEO meter (often prepaid), a shared or sub-meter, Camwater or a "
        "borehole (forage) for water, or included in the rent — and the monthly "
        "amount only if a fixed one is asked on top. Own or shared meters mean "
        "charging_policy EXTRA; included means INCLUDED"
    ),
    "ask.deposit": (
        "the refundable caution you ask of your tenant, in MONTHS of rent. In "
        "this market the caution is counted in months and is paid on top of "
        "the advance: six months of advance plus two of caution means eight "
        "months handed over, two of which come back when the tenant leaves. "
        "Zero is a valid answer. If the reply is an amount in francs, ask "
        "again for the number of months."
    ),
    "ask.advance": (
        "how many months of rent the tenant must pay when moving in, and whether "
        "there is a caution on top — one question for both, as a landlord says "
        "it: '6 mois, pas de caution' is advance_months 6 and deposit_months 0; "
        "'6 mois et 2 mois de caution' is advance_months 6 and deposit_months 2. "
        "Never suggest a number of months: the landlord says it himself"
    ),
    "ask.security": "whether your property has a guard and whether it has parking",
    "ask.minimum_duration": "the minimum duration you want for the rental",
    "ask.availability": (
        "when your property becomes available (a date, or immediately)"
    ),
    "ask.conditions": (
        "your entry conditions: the advance in months, the agency or lease fee, "
        "and the type of tenant you accept"
    ),
}
_TENANT_TOPICS: dict[str, str] = {
    "ask.property_type": "the type of property you are looking for",
    "ask.standing": "whether you want a modern property or not",
    "ask.location": (
        "the area you want to live in, near your workplace for example — not "
        "where you are right now. Suggest first sharing the WhatsApp location "
        "while standing in that area: it is the most precise; naming the "
        "neighbourhood works too"
    ),
    "ask.rent": "the maximum monthly rent you are willing to pay, in XAF",
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
    "ask.quantity": "quantity",
    "ask.deposit": "deposit_months",
    "ask.advance": "advance_months (and deposit_months when a caution is mentioned)",
    "ask.security": "amenities.security and amenities.parking",
    "ask.minimum_duration": "minimum_duration_months",
    "ask.availability": "availability",
    "ask.conditions": "conditions",
    # The search questions bind to the same entities: a tenant answering a
    # budget question with "150" is giving a price, exactly as a landlord is.
    "ask.search_type": "property_type",
    "ask.search_location": "city and neighbourhood",
    "ask.search_budget": "price",
    "ask.search_advance": "advance_months",
    "ask.search_choice": "property_choice",
    # No entity: the answer *is* the intent, which is why the gloss names the
    # two allowed ones. A COLLECT_ intent here would re-ask the same question.
    "ask.publish_or_search": "the intent itself (CREATE_PROPERTY or PROPERTY_SEARCH)",
}

RESPONSE_SCHEMA: dict[str, Any] = {
    "type": "object",
    "required": ["intent", "confidence", "entities"],
    "properties": {
        "intent": {"type": "string", "enum": list(ALLOWED_INTENTS)},
        "confidence": {"type": "number", "minimum": 0, "maximum": 1},
        "language": {"type": "string"},
        "reply": {"type": "string"},
        # The 'field' (from required_fields) the inline 'reply' asks about, or
        # null. Read back so the sentence and the recorded question cannot drift
        # apart: the writer names its question, the code checks it is the one it
        # decided on.
        "asking": {"type": "string"},
        "entities": {
            "type": "object",
            "properties": {
                "property_type": {"type": "string"},
                "standing": {"type": "string", "enum": ["MODERN", "NON_MODERN"]},
                "city": {"type": "string"},
                "neighbourhood": {"type": "string"},
                # A tenant may accept several: "Bonamoussadi ou bien Makepe".
                # Every one he accepts, including those already known when he
                # adds one ("Logbessou aussi"); ``neighbourhood`` is the first.
                "neighbourhoods": {"type": "array", "items": {"type": "string"}},
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
                # Entry money in months of rent, which is how this market
                # states it. ``deposit`` survives only so that an answer given
                # in francs is visible and can be refused, instead of being
                # read as a number of months.
                "deposit_months": {"type": "integer"},
                "advance_months": {"type": "integer"},
                "deposit": {"type": "integer"},
                "quantity": {"type": "integer"},
                # Which line of the result list the tenant picked, one-based.
                "property_choice": {"type": "integer"},
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
    exchanges: list[Exchange] | None = None,
) -> Prompt:
    """Assemble the request, with the state of the conversation as context.

    The context is what turns "Bastos" from noise into an answer: the model can
    see that it is replying to the location question of an open listing, and
    fill the matching entity instead of guessing an intent from three words.

    The phone number is never part of the prompt: the model has no business
    knowing who it is talking to, and prompts end up in vendor logs.
    """
    public_known = public_known_facts(known_facts)
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
        # No task yet, no form: shown the landlord's fields, a "bonjour"
        # was answered "quel type de propriété tu veux proposer ?".
        "required_fields": (
            [] if flow is FlowName.SUPPORT else _missing_fields(flow, public_known)
        ),
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
    if exchanges:
        payload["recent_messages"] = exchanges_as_json(exchanges)
    return Prompt(
        system=SYSTEM_PROMPT,
        user=json.dumps(payload, ensure_ascii=False),
        response_schema=RESPONSE_SCHEMA,
    )


# Keys the pipeline keeps in the session for its own use, never for the model:
# leaking "next_question" into known_facts invites the model to treat an internal
# variable as a fact about the property.
_PROMPT_INTERNAL_KEYS = frozenset({"next_question", "abort_reason"})


def public_known_facts(known_facts: dict[str, Any]) -> dict[str, Any]:
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
    exchanges: list[Exchange] | None = None,
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
                        **public_known_facts(known_facts),
                        **(dict(accepted_facts) if accepted else {}),
                    },
                },
                # None means "the message was not understood": ask for a rephrase
                # rather than posing a question the platform never reached.
                "ask_about": question_topic(next_key, flow),
                "recent_turns": _turns_as_json(history),
                "recent_messages": exchanges_as_json(exchanges or []),
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


def exchanges_as_json(exchanges: list[Exchange]) -> list[dict[str, str]]:
    """The thread as the model reads it: who said what, oldest first."""
    return [{"from": line.speaker, "text": line.text} for line in exchanges]
