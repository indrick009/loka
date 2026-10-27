"""A shared WhatsApp position, read as the answer to the location question."""

from __future__ import annotations

from typing import Any

from loka.bounded_contexts.conversation.application.use_cases.analyse_conversation_message.turn import (  # noqa: E501
    Reading,
)
from loka.bounded_contexts.conversation.domain.value_objects.inbound_message import SharedLocation
from loka.bounded_contexts.housing.conversation.turns.places import (
    neighbourhood_from_label,
)


def shared_location(point: SharedLocation) -> Reading:
    """``confidence`` is 1.0 because nothing was inferred.

    The user pressed "send location" and these are the coordinates WhatsApp
    attached. The place name travels as a neighbourhood *candidate* — the
    extractor only keeps it if the district index recognises it, so a shop's
    name on a map never becomes a quartier.

    A point outside every known market yields no city, the extractor rejects
    the location, and the question stays open. That is the honest outcome:
    there is no geocoder here, and naming a city we cannot justify would file
    the listing in a town the landlord never mentioned.
    """
    entities: dict[str, Any] = {"latitude": point.latitude, "longitude": point.longitude}
    neighbourhood = neighbourhood_from_label(point.label)
    if neighbourhood is not None:
        entities["neighbourhood"] = neighbourhood
    return Reading(
        intent="COLLECT_LOCATION",
        confidence=1.0,
        entities=entities,
        # Answered by stock copy, in "vous", with no word about the pin: the
        # rewriter is told what he sent so the reply can say so.
        media_marker="(position WhatsApp partagée)",
    )
