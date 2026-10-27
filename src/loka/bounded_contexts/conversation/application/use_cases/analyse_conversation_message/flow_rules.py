"""Which flow a turn belongs to, and what it may build on."""

from __future__ import annotations

from typing import Any

from loka.bounded_contexts.conversation.domain.entities.conversation_session import (
    ConversationSession,
    FlowName,
    FlowStep,
)
from loka.bounded_contexts.conversation.domain.services.fact_extraction import (
    FLOW_BY_OPENING_INTENT,
    LISTING_FLOWS,
    PROPERTY_REQUIREMENTS,
    SEARCH_REQUIREMENTS,
)

# Where a listing or a search has nothing left to collect.
FINISHED_STEPS = frozenset({FlowStep.PUBLISHED, FlowStep.DONE})


def _has_listing_facts(context: dict[str, object]) -> bool:
    return any(name in context for name, _ in PROPERTY_REQUIREMENTS)


def opens_new_flow(session: ConversationSession, intent: str) -> bool:
    """Whether this intent starts a *different* flow.

    A flow switch resets the context, so it is only allowed while the current
    flow has collected nothing yet. Otherwise a landlord half-way through a
    listing whose "je loue" is read as a tenant search would be silently thrown
    into the opposite role — the role is set by the facts already given, not by
    the model's reading of one ambiguous verb.
    """
    opening_flow = FLOW_BY_OPENING_INTENT.get(intent)
    if opening_flow is None:
        return False
    if session.flow is opening_flow:
        # The same flow opened again once its task is over is a new task:
        # observed live, "j'ai encore un autre studio" after a publication
        # reused the published listing's facts and attached its photos to it.
        return session.step in FINISHED_STEPS
    return not (session.flow in LISTING_FLOWS and _has_listing_facts(session.context))


def target_flow(session: ConversationSession, intent: str) -> FlowName:
    """The flow this turn belongs to, including one it is about to open.

    Needed wherever the answer must be phrased *before* the session moves: the
    first search message still reads as SUPPORT in the session, but its next
    question is a tenant's. Phrasing a reply from ``session.flow`` on that turn
    hands a tenant the landlord's form — "standing", "charges", "deposit" — and
    the model then asks one of those instead of the question it was given.

    One function for the rule, deliberately: it used to exist twice, the two
    copies drifted, and the reply prompt ended up using neither.
    """
    opening_flow = FLOW_BY_OPENING_INTENT.get(intent)
    if opening_flow is not None and opens_new_flow(session, intent):
        return opening_flow
    return session.flow


def carried_question(session: ConversationSession) -> str | None:
    """The question still open from an earlier turn, if any.

    One reader for two callers on purpose: the sentence the user receives and
    the question the session records are decided from the same value, so they
    cannot end up describing different questions.
    """
    carried = session.context.get("next_question")
    return carried if isinstance(carried, str) else None


def facts_worth_carrying(context: dict[str, Any]) -> dict[str, Any]:
    """The real facts in a context, without the platform's bookkeeping.

    ``next_question`` is a question key and ``_``-prefixed keys are internal —
    carried into a fresh flow they would make the new form believe a question
    was asked, or offer a result list from a search that is over.
    """
    return {
        name: value
        for name, value in context.items()
        if name != "next_question" and not str(name).startswith("_")
    }


def known_facts(session: ConversationSession, intent: str) -> dict[str, Any]:
    """What this turn may build on: nothing from an abandoned attempt.

    One exception, and it is not an abandoned attempt at all: a user answering
    "publier ou chercher ?" is finishing the sentence he started. Dropping what
    he said a moment earlier would ask him for the studio he has just named.
    """
    if session.step is FlowStep.CLARIFY_ROLE:
        return dict(session.context)
    return {} if opens_new_flow(session, intent) else dict(session.context)


def is_search(flow: FlowName) -> bool:
    """Which form a prompt shows: the tenant's search, or the landlord's.

    Every other flow, SUPPORT included, is shown the landlord's fields.
    """
    return flow is FlowName.PROPERTY_SEARCH


def field_for_step(step: FlowStep, flow: FlowName) -> str | None:
    """The form field name a step collects, in this flow's form.

    The name is what the writer is handed in ``required_fields`` and reports
    back as ``asking``; ``None`` means the step is outside the form, so its
    question is not one the writer is ever asked to phrase.
    """
    form = SEARCH_REQUIREMENTS if is_search(flow) else PROPERTY_REQUIREMENTS
    for name, form_step in form:
        if form_step is step:
            return name
    return None
