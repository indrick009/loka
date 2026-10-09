"""Cameroonian real-estate knowledge, as data rather than prompt prose.

Everything a model would otherwise have to *remember* about this market — which
districts belong to which city, what a month of rent costs, what "mil" means —
lives here, in code, with a source and a confidence on every entry. The point is
not to teach the model the market; it is that the model must never be the
authority on it. These functions are the authority, and they are testable.

Confidence vocabulary:

* ``VERIFIED`` — backed by a legal text or an official source.
* ``OBSERVED`` — reported consistently by several market listings/blogs.
* ``TO_CONFIRM`` — plausible but from a single or secondary source; usable as a
  hint, never as a hard rule.
"""

from __future__ import annotations

from loka.bounded_contexts.ai.domain.knowledge.market import (
    CITY_ALIASES,
    EXPRESSIONS,
    NEIGHBOURHOODS,
    PRICE_BANDS_XAF,
    SOURCES,
    Confidence,
    Expression,
    Neighbourhood,
    PriceBand,
    price_reference,
    resolve_city,
    resolve_neighbourhood,
)

__all__ = [
    "CITY_ALIASES",
    "EXPRESSIONS",
    "NEIGHBOURHOODS",
    "PRICE_BANDS_XAF",
    "SOURCES",
    "Confidence",
    "Expression",
    "Neighbourhood",
    "PriceBand",
    "price_reference",
    "resolve_city",
    "resolve_neighbourhood",
]
