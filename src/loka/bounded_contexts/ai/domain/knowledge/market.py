"""Sourced reference data for the Cameroonian rental market.

This module is deliberately dependency-free (no property enums, no ORM): it is
plain data plus pure lookups, so it can be read and tested in isolation and used
by both the deterministic classifier and the fact extractor.

The two hard problems it solves:

1. **A quartier is not a city.** Cameroonians answer "Bastos", not "Yaoundé,
   Bastos". Without a district-to-city map, a perfectly good answer to the
   location question is unusable and the form asks again forever.
2. **A number is not a price.** "50 mil" is 50 000 FCFA here, a "chambre salon"
   is a one-bedroom-plus-living-room, and an entry cost is an *avance* of several
   months on top of a *caution*. These are conventions, not model knowledge.
"""

from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass
from enum import StrEnum


class Confidence(StrEnum):
    """How much a fact may be trusted. Drives whether it is a rule or a hint."""

    VERIFIED = "VERIFIED"
    OBSERVED = "OBSERVED"
    TO_CONFIRM = "TO_CONFIRM"


@dataclass(frozen=True, slots=True)
class Source:
    key: str
    label: str
    url: str | None = None


# Every entry below points at one of these by key, so a claim can always be
# traced back. Legal texts are VERIFIED authority; blogs/listings are OBSERVED.
SOURCES: dict[str, Source] = {
    "loi-2014-023": Source(
        key="loi-2014-023",
        label="Loi n°2014/023 du 24 décembre 2014 régissant les baux à usage d'habitation",
    ),
    "loi-2009-010": Source(
        key="loi-2009-010",
        label="Loi n°2009/010 du 10 juillet 2009 relative à la location-accession",
    ),
    "njikam": Source(
        key="njikam",
        label="Njikam.com — Louer un appartement à Douala (2025)",
        url="https://www.njikam.com/blog/louer-appartement-douala.php",
    ),
    "onviiit": Source(
        key="onviiit",
        label="Onviiit — Le contrat de bail au Cameroun",
        url="https://onviiit.com/2023/07/28/le-contrat-de-bail-au-cameroun/",
    ),
    "lanation": Source(
        key="lanation",
        label="La Nation d'Afrique — Quartiers les plus chers de Douala et Yaoundé",
        url="https://lanationdafrique.net/immobilier-top-10-des-quartiers-les-plus-chers-de-douala-et-yaounde/",
    ),
    "apparts": Source(
        key="apparts",
        label="Apparts-Meubles — Top quartiers pour vivre à Yaoundé et Douala",
        url="https://apparts-meubles.com/top-5-quartiers-pour-vivre-a-yaounde-et-douala/",
    ),
}


@dataclass(frozen=True, slots=True)
class Neighbourhood:
    name: str
    city: str
    source: str
    confidence: Confidence = Confidence.OBSERVED
    premium: bool = False


# The district index. ``premium`` marks the addresses where rents are known to be
# well above the city median; it is OBSERVED and only widens a price band, it
# never rejects a price a user is sure about.
NEIGHBOURHOODS: tuple[Neighbourhood, ...] = (
    # --- Douala ---
    Neighbourhood("Akwa", "Douala", "lanation"),
    Neighbourhood("Bonanjo", "Douala", "lanation", premium=True),
    Neighbourhood("Bonapriso", "Douala", "lanation", premium=True),
    Neighbourhood("Bonamoussadi", "Douala", "apparts", premium=True),
    Neighbourhood("Bonabéri", "Douala", "lanation"),
    Neighbourhood("Bepanda", "Douala", "njikam"),
    Neighbourhood("New Bell", "Douala", "njikam"),
    Neighbourhood("Deïdo", "Douala", "njikam"),
    Neighbourhood("Bali", "Douala", "njikam"),
    Neighbourhood("Cité des Palmiers", "Douala", "njikam"),
    Neighbourhood("Makepe", "Douala", "apparts"),
    Neighbourhood("Mvan", "Douala", "apparts"),
    Neighbourhood("Logbessou", "Douala", "apparts"),
    Neighbourhood("Ndokotti", "Douala", "njikam"),
    Neighbourhood("Kotto", "Douala", "njikam"),
    Neighbourhood("Ndogbong", "Douala", "njikam"),
    Neighbourhood("Yassa", "Douala", "apparts", confidence=Confidence.TO_CONFIRM),
    # --- Yaoundé ---
    Neighbourhood("Bastos", "Yaoundé", "lanation", premium=True),
    Neighbourhood("Nlongkak", "Yaoundé", "lanation", premium=True),
    Neighbourhood("Omnisport", "Yaoundé", "lanation"),
    Neighbourhood("Mvog-Mbi", "Yaoundé", "apparts"),
    Neighbourhood("Mvog-Ada", "Yaoundé", "apparts"),
    Neighbourhood("Nsam", "Yaoundé", "apparts"),
    Neighbourhood("Odza", "Yaoundé", "apparts"),
    Neighbourhood("Emana", "Yaoundé", "apparts"),
    Neighbourhood("Mendong", "Yaoundé", "apparts"),
    Neighbourhood("Ekounou", "Yaoundé", "apparts"),
    Neighbourhood("Ngoa-Ekelle", "Yaoundé", "apparts"),
    Neighbourhood("Tsinga", "Yaoundé", "apparts"),
    Neighbourhood("Biyem-Assi", "Yaoundé", "apparts"),
    Neighbourhood("Etoudi", "Yaoundé", "apparts"),
    Neighbourhood("Mimboman", "Yaoundé", "apparts"),
    Neighbourhood("Nkolbisson", "Yaoundé", "apparts"),
    Neighbourhood("Etoa-Meki", "Yaoundé", "apparts"),
    Neighbourhood("Briqueterie", "Yaoundé", "apparts"),
    Neighbourhood("Madagascar", "Yaoundé", "apparts"),
    Neighbourhood("Mokolo", "Yaoundé", "apparts"),
    # --- Secondary cities ---
    Neighbourhood("Molyko", "Buea", "apparts"),
    Neighbourhood("Great Soppo", "Buea", "apparts"),
    Neighbourhood("Bomaka", "Buea", "apparts", confidence=Confidence.TO_CONFIRM),
    Neighbourhood("Djeleng", "Bafoussam", "apparts", confidence=Confidence.TO_CONFIRM),
    Neighbourhood("Tamdja", "Bafoussam", "apparts", confidence=Confidence.TO_CONFIRM),
)

# Canonical spellings and common variants. Keys are normalised; values are the
# canonical city name used everywhere downstream.
CITY_ALIASES: dict[str, str] = {
    "douala": "Douala",
    "yaounde": "Yaoundé",
    "yaunde": "Yaoundé",
    "bafoussam": "Bafoussam",
    "buea": "Buea",
    "kribi": "Kribi",
    "limbe": "Limbé",
    "garoua": "Garoua",
    "ngaoundere": "Ngaoundéré",
    "bamenda": "Bamenda",
    "maroua": "Maroua",
    "bertoua": "Bertoua",
    "ebolowa": "Ebolowa",
    "kumba": "Kumba",
    "dschang": "Dschang",
}

@dataclass(frozen=True, slots=True)
class PriceBand:
    """A wide monthly-rent range in XAF; an anchor, never a rule."""

    low_xaf: int
    high_xaf: int
    source: str
    confidence: Confidence = Confidence.OBSERVED


# Base band per property type (values mirror ``PropertyType``). These are the
# magnitudes the model needs so it never reads a bare "50" as fifty francs.
PRICE_BANDS_XAF: dict[str, PriceBand] = {
    "ROOM": PriceBand(10_000, 60_000, "njikam"),
    "STUDIO": PriceBand(20_000, 150_000, "njikam"),
    "APARTMENT": PriceBand(40_000, 400_000, "njikam", Confidence.TO_CONFIRM),
    "HOUSE": PriceBand(80_000, 800_000, "njikam", Confidence.TO_CONFIRM),
    "DUPLEX": PriceBand(150_000, 1_500_000, "lanation", Confidence.TO_CONFIRM),
    "COMMERCIAL": PriceBand(50_000, 1_000_000, "njikam", Confidence.TO_CONFIRM),
}

# Premium addresses widen the band rather than replacing it, because the source
# gives a range, not a coefficient.
_PREMIUM_FACTOR = 2


@dataclass(frozen=True, slots=True)
class Expression:
    """A local wording and what it means for the platform."""

    phrase: str
    meaning: str
    source: str
    confidence: Confidence = Confidence.OBSERVED


EXPRESSIONS: tuple[Expression, ...] = (
    Expression("mil", "thousands (50 mil = 50 000 FCFA), never millions", "njikam"),
    Expression("avance", "months of rent paid up front before moving in", "onviiit"),
    Expression("caution", "refundable security deposit (1 to 2 months)", "onviiit"),
    Expression("frais de bail / frais d'agence", "agency or lease contract fee", "onviiit"),
    Expression("chambre salon", "one bedroom plus a living room", "njikam"),
    Expression("sdb", "salle de bain (bathroom)", "njikam"),
    Expression("rdc", "rez-de-chaussée (ground floor)", "njikam"),
    Expression("entrée / conditions d'entrée", "move-in cost: avance, caution, fees", "onviiit"),
    Expression("carré / en face de / derrière", "colloquial address hint", "njikam",
               Confidence.TO_CONFIRM),
)

# Entry conventions observed across listings; the platform asks for them so a
# published listing can state its real move-in cost.
ADVANCE_MONTHS_TYPICAL = (3, 6)  # njikam / onviiit — advance, OBSERVED
CAUTION_MONTHS_TYPICAL = (1, 2)  # onviiit — caution, OBSERVED


def normalize(text: str) -> str:
    """Fold accents, punctuation and case so "Yaoundé" and "yaounde" match."""
    folded = (
        unicodedata.normalize("NFKD", str(text))
        .encode("ascii", "ignore")
        .decode("ascii")
        .casefold()
        .replace("'", " ")
        .replace("-", " ")
    )
    return re.sub(r"\s+", " ", folded).strip()


_CITY_BY_NEIGHBOURHOOD = {normalize(n.name): n for n in NEIGHBOURHOODS}
_PREMIUM_NEIGHBOURHOODS = frozenset(
    normalize(n.name) for n in NEIGHBOURHOODS if n.premium
)


def resolve_city(text: str) -> str | None:
    """The canonical city for a city name or alias, else ``None``."""
    return CITY_ALIASES.get(normalize(text))


def resolve_neighbourhood(text: str) -> str | None:
    """The city a district belongs to, else ``None``.

    Only a *known* district resolves. An unknown hint stays a hint: promoting
    "Zzzville" to a city is exactly how a wrong listing enters search.
    """
    entry = _CITY_BY_NEIGHBOURHOOD.get(normalize(text))
    return entry.city if entry is not None else None


def canonical_neighbourhood(text: str) -> str | None:
    """The properly-cased district name, else ``None``."""
    entry = _CITY_BY_NEIGHBOURHOOD.get(normalize(text))
    return entry.name if entry is not None else None


def is_premium(text: str) -> bool:
    return normalize(text) in _PREMIUM_NEIGHBOURHOODS


def price_reference(
    property_type: str | None,
    *,
    city: str | None = None,
    neighbourhood: str | None = None,
) -> PriceBand | None:
    """The monthly-rent band for a type, widened at known premium addresses.

    ``city`` is accepted for future per-city tuning; today the bands are national
    and only the neighbourhood changes the magnitude, which keeps the data honest
    about what the sources actually support.
    """
    band = PRICE_BANDS_XAF.get((property_type or "").upper())
    if band is None:
        return None
    if neighbourhood and is_premium(neighbourhood):
        return PriceBand(
            low_xaf=band.low_xaf * _PREMIUM_FACTOR,
            high_xaf=band.high_xaf * _PREMIUM_FACTOR,
            source=band.source,
            confidence=Confidence.TO_CONFIRM,
        )
    return band


__all__ = [
    "ADVANCE_MONTHS_TYPICAL",
    "CAUTION_MONTHS_TYPICAL",
    "CITY_ALIASES",
    "EXPRESSIONS",
    "NEIGHBOURHOODS",
    "PRICE_BANDS_XAF",
    "SOURCES",
    "Confidence",
    "Expression",
    "Neighbourhood",
    "PriceBand",
    "canonical_neighbourhood",
    "is_premium",
    "normalize",
    "price_reference",
    "resolve_city",
    "resolve_neighbourhood",
]
