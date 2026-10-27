"""The housing forms: what is asked, in which order, under which key."""

from __future__ import annotations

from loka.bounded_contexts.conversation.domain.entities.conversation_session import (
    FlowName,
    FlowStep,
)

# The order is the conversation order: it decides what is asked next. It matches
# how people describe a home here: the type first ("appart", "studio", "chambre"),
# then modern or not, then where it is, then the rent. The bathroom count and the
# surface are deliberately *not* asked: they are rarely known and rarely used,
# and the answer that sells the place is the photos.
#
# The entry money comes after the rent because in this market the monthly price
# is only half the story: the real move-in cost is an *avance* (several months
# up front) plus a *caution* (refundable, on top of the advance) plus the
# monthly charges. Both are counted in months of rent, not in francs.
#
# This is the ten-step form of conception_metier/kora-housing-v1.md §4, in its
# order. Three fields the form used to ask for — the minimum duration, the
# availability date and the free-text conditions — are deliberately absent:
# Kora introduces a landlord to a tenant and does not negotiate a lease (§10).
# Their steps, entities and setters all remain, so a landlord who volunteers
# "disponible immédiatement" still has it recorded. Optional, not asked.
PROPERTY_REQUIREMENTS: tuple[tuple[str, FlowStep], ...] = (
    ("property_type", FlowStep.COLLECT_PROPERTY_TYPE),
    ("standing", FlowStep.COLLECT_STANDING),
    ("quantity", FlowStep.COLLECT_QUANTITY),
    ("location", FlowStep.COLLECT_LOCATION),
    ("rent", FlowStep.COLLECT_PRICE),
    ("charges", FlowStep.COLLECT_CHARGES),
    # One question for both, the way a landlord says it: "6 mois, pas de
    # caution" or "6 mois et 2 mois de caution". The caution is only asked on
    # its own when the answer left it out.
    ("advance_months", FlowStep.COLLECT_ADVANCE),
    ("deposit_months", FlowStep.COLLECT_DEPOSIT),
    ("security", FlowStep.COLLECT_SECURITY),
)


# The tenant's form is a different shape. A search only needs what narrows the
# catalogue — the type, the place, the budget — and every further field a
# listing collects (charges, deposit, dates, conditions) is a question a
# searching tenant cannot answer, because it describes someone else's property.
# Asking them would stall the search on a field search does not have.
#
# The terminal step is ``PRESENT_RESULTS`` rather than a question: reaching it
# means the criteria are complete, and the next move is to read the catalogue.
#
# The advance is the one entry condition a tenant *can* answer — it is about
# his own savings, not about the lodging — and §3 asks it: in this market the
# months handed over up front rule out more lodgings than the rent does.
# Session context key: the other lodgings a landlord described, still to be
# published one at a time once the form under way is (§5).
NEXT_LODGINGS_KEY = "_next_lodgings"


SEARCH_REQUIREMENTS: tuple[tuple[str, FlowStep], ...] = (
    ("property_type", FlowStep.COLLECT_PROPERTY_TYPE),
    ("location", FlowStep.COLLECT_LOCATION),
    ("rent", FlowStep.COLLECT_PRICE),
    ("advance_months", FlowStep.COLLECT_ADVANCE),
)


# A stable key, never a sentence: copy and translations belong to the outbound
# layer. This stage decides *what* is missing, not how to phrase it.
QUESTION_BY_STEP: dict[FlowStep, str] = {
    FlowStep.COLLECT_PROPERTY_TYPE: "ask.property_type",
    FlowStep.COLLECT_STANDING: "ask.standing",
    FlowStep.COLLECT_LOCATION: "ask.location",
    FlowStep.COLLECT_PRICE: "ask.rent",
    FlowStep.COLLECT_FEATURES: "ask.features",
    FlowStep.COLLECT_CHARGES: "ask.charges",
    FlowStep.COLLECT_QUANTITY: "ask.quantity",
    FlowStep.COLLECT_DEPOSIT: "ask.deposit",
    FlowStep.COLLECT_ADVANCE: "ask.advance",
    FlowStep.COLLECT_SECURITY: "ask.security",
    FlowStep.COLLECT_MINIMUM_DURATION: "ask.minimum_duration",
    FlowStep.COLLECT_AVAILABILITY: "ask.availability",
    FlowStep.COLLECT_CONDITIONS: "ask.conditions",
    FlowStep.CLARIFY_ROLE: "ask.publish_or_search",
    FlowStep.CONFIRM_PROPERTY: "ask.confirm_property",
    # Reached once the listing is saved but the quality gate still wants photos.
    # With no key here the landlord was told "c'est bien noté" and never asked
    # for the one thing standing between a draft and a published listing.
    FlowStep.COLLECT_MEDIA: "ask.media",
    FlowStep.PUBLISHED: "confirm.published",
}


# The same step asks opposite people opposite things: a landlord is asked where
# the property *is* and what rent they *want*, a tenant where they want to look
# and what budget they *have*. No single sentence serves both, so the search
# flow carries its own keys and the outbound layer words each of them.
SEARCH_QUESTION_BY_STEP: dict[FlowStep, str] = {
    FlowStep.COLLECT_PROPERTY_TYPE: "ask.search_type",
    FlowStep.COLLECT_LOCATION: "ask.search_location",
    FlowStep.COLLECT_PRICE: "ask.search_budget",
    FlowStep.COLLECT_ADVANCE: "ask.search_advance",
    FlowStep.COLLECT_SEARCH_CRITERIA: "ask.search_criteria",
    FlowStep.PRESENT_RESULTS: "ask.search_results",
    FlowStep.COLLECT_PROPERTY_CHOICE: "ask.search_choice",
}


def question_key_for(step: FlowStep | None, flow: FlowName | None = None) -> str | None:
    """The stable key the outbound layer words for this step *in this flow*.

    ``flow`` may be omitted where only one reading is possible; it must be
    passed wherever the same step can be asked of either side, because the key
    is what selects the sentence a tenant reads versus the one a landlord does.
    """
    if step is None:
        return None
    if flow is FlowName.PROPERTY_SEARCH:
        search_key = SEARCH_QUESTION_BY_STEP.get(step)
        if search_key is not None:
            return search_key
    return QUESTION_BY_STEP.get(step)


def question_to_ask(
    *, next_step: FlowStep | None, flow: FlowName, pending: str | None
) -> str | None:
    """The question this turn puts to the user, decided once and in one place.

    The form's next question comes first. When the turn moved no field — a
    thank-you, a confirmation, a question about what Kora already knows — the
    one still open is asked again instead of nothing, because a turn that
    acknowledges and then asks nothing reads as the end of the conversation.
    That was the single most common dead end in the thread: "c'est bien noté,
    je vous pose la question suivante dès que j'ai besoin d'une précision",
    followed by silence.

    ``None`` means there is genuinely nothing open — a greeting on a fresh
    session — and the outbound layer answers with what Kora can do instead.

    Callers record exactly what this returns, which is what keeps the stored
    pending question and the question actually asked from drifting apart.

    A step that *was* decided but asks nothing — the demand is recorded and Kora
    now waits — closes the old question instead of inheriting it. Observed
    live: after "votre demande est enregistrée" the session still awaited the
    budget, every later reply went out as that question, and a tenant's next
    "70 mille" would have silently rewritten his budget.
    """
    if next_step is not None:
        return question_key_for(next_step, flow)
    return pending
