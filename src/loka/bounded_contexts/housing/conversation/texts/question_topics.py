"""What each question is about, in words the writer can rephrase."""

from __future__ import annotations

from loka.bounded_contexts.conversation.domain.entities.conversation_session import (
    FlowName,
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
