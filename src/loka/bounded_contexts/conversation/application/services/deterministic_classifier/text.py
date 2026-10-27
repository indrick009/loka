"""Normalised text, and the amounts and entities read out of it."""

from __future__ import annotations

import re
from typing import Any

from loka.bounded_contexts.conversation.application.services.deterministic_classifier.patterns import (  # noqa: E501
    _ADDS_A_PLACE,
    ADDS_NEIGHBOURHOODS,
    AMOUNT_PATTERN,
    BATHROOM_PATTERN,
    BEDROOM_PATTERN,
    GROUPED_AMOUNT_PATTERN,
    LOCATION_HINT_PATTERN,
    MODERN_PATTERN,
    NON_MODERN_PATTERN,
    PLAIN_AMOUNT_PATTERN,
    SHORTHAND_MILLION_PATTERN,
    SHORTHAND_THOUSAND_PATTERN,
    SURFACE_PATTERN,
)
from loka.bounded_contexts.conversation.domain.knowledge import market
from loka.bounded_contexts.conversation.domain.services.fact_extraction import (
    MIN_PLAUSIBLE_RENT_XAF,
)


def _extract_entities(normalized: str) -> dict[str, object]:
    entities: dict[str, object] = {}
    raw_amount, multiplier = _find_amount(normalized)
    if raw_amount is not None:
        value = _parse_amount(raw_amount) * multiplier
        if value >= MIN_PLAUSIBLE_RENT_XAF:
            entities["price"] = value
    bedrooms = BEDROOM_PATTERN.search(normalized)
    if bedrooms:
        entities["bedrooms"] = int(bedrooms.group("count"))
    bathrooms = BATHROOM_PATTERN.search(normalized)
    if bathrooms:
        entities["bathrooms"] = int(bathrooms.group("count"))
    surface = SURFACE_PATTERN.search(normalized)
    if surface:
        entities["surface_area"] = int(surface.group("area"))
    if NON_MODERN_PATTERN.search(normalized):
        entities["standing"] = "NON_MODERN"
    elif MODERN_PATTERN.search(normalized):
        entities["standing"] = "MODERN"
    location = _location_entity(normalized)
    if location is not None:
        entities.update(location)
        if location["neighbourhood"] and _ADDS_A_PLACE.search(normalized):
            # The rules cannot see what is already known; they say that
            # this place comes on top of it, and the pipeline does the sum.
            entities[ADDS_NEIGHBOURHOODS] = True
    else:
        hint = LOCATION_HINT_PATTERN.search(normalized)
        if hint:
            entities["location_hint"] = hint.group(0).title()
    return entities


def _find_amount(normalized: str) -> tuple[str | None, int]:
    """The first amount in the message, with its locale multiplier.

    Ordered most-specific first: an explicit "fcfa" beats a shorthand, and a
    million ("million"/"mio") beats a thousand ("mil"/"k") so "50 million is
    not read as 50 000.
    """
    for pattern, multiplier in (
        (AMOUNT_PATTERN, 1),
        (SHORTHAND_MILLION_PATTERN, 1_000_000),
        (SHORTHAND_THOUSAND_PATTERN, 1_000),
    ):
        match = pattern.search(normalized)
        if match:
            return match.group("amount"), multiplier
    for pattern in (GROUPED_AMOUNT_PATTERN, PLAIN_AMOUNT_PATTERN):
        match = pattern.search(normalized)
        if match:
            return match.group(1), 1
    return None, 1


def _first_match(patterns: dict[str, re.Pattern[str]], normalized: str) -> str | None:
    for label, pattern in patterns.items():
        if pattern.search(normalized):
            return label
    return None


def _normalize(text: str) -> str:
    folded = (
        text.strip()
        .casefold()
        .replace("\u202f", " ")
        .replace("\u2019", "'")
    )
    return re.sub(r"\s+", " ", folded)


def _find_digits(normalized: str) -> str | None:
    found = re.search(r"\d", normalized)
    return found.group(0) if found else None


def _parse_amount(raw: str) -> int:
    digits = re.sub(r"[^\d]", "", raw)
    return int(digits) if digits else 0


def _location_entity(normalized: str) -> dict[str, Any] | None:
    """The city and the districts a message names, or ``None``.

    Scans the words longest-first so a two-word district ("New Bell") beats its
    single word, and resolves both canonical cities and every district in the
    market index — so "yaounde nkoabang" closes the location question on Yaoundé
    even when the district itself is not in the index. Only *known* places
    resolve: promoting an unknown word to a city is how a wrong search is born.

    Every district is kept, in the order written: "vers Bonamoussadi ou bien
    Makepe" is two places a tenant accepts, and returning the first one alone
    silently narrowed his demand. A district in another city than the one named
    is dropped rather than guessed into it.
    """
    # Punctuation is shed so "à Douala, c'est comment" still names Douala.
    words = [word.strip(".,;:!?()\"") for word in normalized.split()]
    taken: set[int] = set()
    city: str | None = None
    districts: list[tuple[int, str, str]] = []
    for size in (3, 2, 1):
        for start in range(0, len(words) - size + 1):
            span = set(range(start, start + size))
            if span & taken:
                continue
            candidate = " ".join(words[start : start + size])
            named_city = market.resolve_city(candidate)
            if named_city is not None:
                city = city or named_city
                taken |= span
                continue
            district_city = market.resolve_neighbourhood(candidate)
            if district_city is not None:
                name = market.canonical_neighbourhood(candidate) or candidate
                districts.append((start, name, district_city))
                taken |= span
    if city is None and not districts:
        return None
    districts.sort()
    city = city or districts[0][2]
    names = [name for _, name, district_city in districts if district_city == city]
    location: dict[str, Any] = {"city": city, "neighbourhood": names[0] if names else ""}
    if len(names) > 1:
        location["neighbourhoods"] = names
    return location
