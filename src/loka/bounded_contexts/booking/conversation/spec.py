"""What Booking tells the conversation engine about itself."""

from __future__ import annotations

import re
from datetime import date
from typing import Any

from loka.bounded_contexts.conversation.application.plugs.forms import common_facts, text_of
from loka.bounded_contexts.conversation.application.plugs.spec import DomainSpec, FormField
from loka.bounded_contexts.conversation.domain.entities.conversation_session import FlowName
from loka.bounded_contexts.conversation.domain.services.extracted_facts import ExtractedFacts
from loka.shared.domain.knowledge import market

TRIP_FORM = (
    FormField("origin", "the city the traveller leaves from", {
        "fr": "Tu pars de quelle ville ?", "en": "Which city are you leaving from?",
    }),
    FormField("destination", "the city the traveller goes to", {
        "fr": "Et tu vas où ?", "en": "And where are you going?",
    }),
    FormField("travel_date", "the travel date", {
        "fr": "Tu voyages quel jour ? Par exemple : demain, samedi, ou le 15/11.",
        "en": "Which day are you travelling? For example: tomorrow, Saturday, or 15/11.",
    }),
    FormField("passengers", "how many travellers", {
        "fr": "Combien de places faut-il réserver ?", "en": "How many seats should I book?",
    }),
)

RULES = (
    " Booking: someone who wants to travel by bus from one city to another "
    "(Yaoundé, Douala, Bafoussam…) is BOOKING_TRIP. 'origin' and 'destination' "
    "are city names; 'travel_date' is the date as YYYY-MM-DD worked out from "
    "'today' ('demain' is today plus one day, 'samedi' the next Saturday); "
    "'passengers' the number of travellers. Seat numbers picked on the bus plan "
    "go in 'seats'; the travellers' full names in 'passenger_names'; a Mobile "
    "Money number to pay with in 'payer_phone'. A departure picked from a list "
    "goes in 'choice'."
)

ENTITIES: dict[str, Any] = {
    "origin": {"type": "string"},
    "destination": {"type": "string"},
    "travel_date": {"type": "string"},
    "passengers": {"type": "integer"},
    "seats": {"type": "array", "items": {"type": "integer"}},
    "passenger_names": {"type": "array", "items": {"type": "string"}},
    "payer_phone": {"type": "string"},
}


def city(raw: Any) -> str | None:
    text = text_of(raw, longest=60)
    if not text:
        return None
    return market.resolve_city(text) or market.resolve_neighbourhood(text) or text.title()


def validate(entities: dict[str, Any], flow: FlowName) -> ExtractedFacts:
    facts, rejected = common_facts({k: v for k, v in entities.items() if k != "city"})
    facts.pop("place", None)
    rejected.pop("place", None)
    for name in ("origin", "destination"):
        found = city(entities.get(name))
        if found:
            facts[name] = found
    raw_date = str(entities.get("travel_date") or "")
    try:
        if raw_date:
            facts["travel_date"] = date.fromisoformat(raw_date[:10]).isoformat()
    except ValueError:
        rejected["travel_date"] = "not a date"
    passengers = entities.get("passengers")
    if isinstance(passengers, int) and not isinstance(passengers, bool) and 1 <= passengers <= 9:
        facts["passengers"] = passengers
    seats = entities.get("seats")
    if isinstance(seats, list):
        picked = [seat for seat in seats if isinstance(seat, int) and 1 <= seat <= 90]
        if picked:
            facts["seats"] = sorted(set(picked))
    names = entities.get("passenger_names")
    if isinstance(names, list):
        kept = [name for name in (text_of(item, longest=80) for item in names) if name]
        if kept:
            facts["passenger_names"] = kept
    phone = re.sub(r"\D", "", str(entities.get("payer_phone") or ""))
    if len(phone) >= 9:
        facts["payer_phone"] = phone
    return ExtractedFacts(facts=facts, rejected=rejected)


BOOKING = DomainSpec(
    name="booking",
    roles={
        FlowName.BOOKING_TRIP: "a traveller booking a bus seat between two cities, "
        "then picking a seat on the bus plan and paying by Mobile Money"
    },
    forms={FlowName.BOOKING_TRIP: TRIP_FORM},
    opening_intents={"BOOKING_TRIP": FlowName.BOOKING_TRIP},
    intents=frozenset({"AFFIRMATIVE", "NEGATIVE", "COLLECT_CHOICE"}),
    rules=RULES,
    entities=ENTITIES,
    validate=validate,
)
