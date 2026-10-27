"""A place said in several messages, kept as one place."""

from __future__ import annotations

from typing import Any

from loka.bounded_contexts.ai.application.services.deterministic_classifier import (
    ADDS_NEIGHBOURHOODS,
)
from loka.bounded_contexts.ai.domain.knowledge import market
from loka.bounded_contexts.messaging.domain.entities.conversation_session import (
    ConversationSession,
)


def neighbourhood_from_label(label: str | None) -> str | None:
    """A quartier the district index recognises inside a WhatsApp place name.

    The label is whatever the network attached — "Bastos", "Yaoundé, Cameroun",
    or a shop's own name — so it is searched for a *known* district and
    otherwise discarded. Passing it through unchecked would turn a boutique's
    signboard into a neighbourhood the catalogue cannot be searched by.
    """
    if not label:
        return None
    for part in label.split(","):
        canonical = market.canonical_neighbourhood(part)
        if canonical is not None:
            return canonical
    return None


def with_known_neighbourhoods(
    entities: dict[str, Any], session: ConversationSession
) -> dict[str, Any]:
    """Add the places already accepted when the user says this one comes on top.

    Only the rules set the marker; the model is asked to list them all itself.
    A place in another city starts over rather than being mixed with these.
    """
    if not entities.get(ADDS_NEIGHBOURHOODS):
        return entities
    merged = {name: value for name, value in entities.items() if name != ADDS_NEIGHBOURHOODS}
    known = session.context.get("location")
    if not isinstance(known, dict) or known.get("city") != merged.get("city"):
        return merged
    earlier = known.get("neighbourhoods") or [known.get("neighbourhood")]
    added = merged.get("neighbourhoods") or [merged.get("neighbourhood")]
    names = [str(name) for name in [*earlier, *added] if name]
    merged["neighbourhood"] = names[0] if names else merged.get("neighbourhood")
    merged["neighbourhoods"] = names
    return merged


def keeping_the_place(entities: dict[str, Any], session: ConversationSession) -> dict[str, Any]:
    """A pin and a district name are two halves of one place: keep both.

    Each used to replace the other. A landlord who shared his location and was
    then asked the neighbourhood lost the pin the moment he typed "Odza"; one
    who typed it first lost the name to the pin, which carries none. Merged
    only when both name the same city — a place in another city starts over.
    """
    known = session.context.get("location")
    if not isinstance(known, dict) or not known.get("city"):
        return entities
    pinned = isinstance(entities.get("latitude"), int | float)
    named = bool(entities.get("neighbourhood") or entities.get("neighbourhoods"))
    if pinned and not named and known.get("neighbourhood"):
        city = market.resolve_city_by_coordinates(entities["latitude"], entities["longitude"])
        if city == known["city"]:
            return {
                **entities,
                "neighbourhood": known["neighbourhood"],
                **(
                    {"neighbourhoods": known["neighbourhoods"]}
                    if known.get("neighbourhoods")
                    else {}
                ),
            }
    if named and not pinned and isinstance(known.get("latitude"), int | float):
        city = entities.get("city") or market.resolve_neighbourhood(
            str(entities.get("neighbourhood") or "")
        )
        if city in (None, "", known["city"]):
            return {
                **entities,
                "city": known["city"],
                "latitude": known["latitude"],
                "longitude": known["longitude"],
            }
    return entities


def one_place(entities: dict[str, Any], session: ConversationSession) -> dict[str, Any]:
    """Both rules, in the order the turn needs them."""
    return keeping_the_place(with_known_neighbourhoods(entities, session), session)
