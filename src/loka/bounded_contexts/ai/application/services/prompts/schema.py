"""The JSON the analysis call must answer with."""

from __future__ import annotations

from typing import Any

from loka.bounded_contexts.ai.application.services.prompts.system import (
    ALLOWED_INTENTS,
)

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
                # A landlord's lodgings that differ by type or rent, in the
                # order he gave them; each becomes its own listing (§5).
                "lodgings": {
                    "type": "array",
                    "items": {
                        "type": "object",
                        "properties": {
                            "property_type": {"type": "string"},
                            "price": {"type": "integer"},
                            "quantity": {"type": "integer"},
                        },
                    },
                },
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
