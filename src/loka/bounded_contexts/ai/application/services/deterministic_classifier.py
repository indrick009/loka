"""Deterministic intent classification.

Two entry points, with different jobs:

* :meth:`DeterministicClassifier.shortcut` is the cheap path used when a model
  is available. It only fires on messages that carry no ambiguity at all — an
  exact "oui"/"non" or a bare amount — where spending a model call would be
  pure waste. Everything else goes to the model first.
* :meth:`DeterministicClassifier.classify` is the fuller rule set used when no
  model is configured, so the platform still answers with regexes alone.

Anything neither can resolve returns ``None``, which is the signal to fall back
rather than guess.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

from loka.bounded_contexts.ai.domain.services.ai_provider import IntentResult
from loka.bounded_contexts.ai.domain.services.fact_extraction import (
    MIN_PLAUSIBLE_RENT_XAF,
)

AFFIRMATIVE = frozenset(
    {
        "oui",
        "ouais",
        "yes",
        "y",
        "ok",
        "okay",
        "daccord",
        "d accord",
        "ca me va",
        "exact",
        "parfait",
        "super",
        "merci",
        "ca marche",
        "bien",
        "valide",
        "confirme",
        "confirmation",
    }
)
NEGATIVE = frozenset(
    {
        "non",
        "no",
        "n",
        "jamais",
        "refuse",
        "refusee",
        "annule",
        "annulee",
        "je ne veux pas",
        "autre chose",
        "stop",
        "laisse tomber",
        "finalement non",
    }
)

# A bare greeting is the single most frequent message that carries no intent at
# all. When no question is open it can be answered from a template, so it must
# not spend a model call.
GREETINGS = frozenset(
    {
        "bonjour",
        "bonsoir",
        "salut",
        "hello",
        "hi",
        "allo",
        "coucou",
        "bonjour monsieur",
        "bonjour madame",
        "bonjour monsieur madame",
        "bjr",
        "slt",
    }
)

AMOUNT_PATTERN = re.compile(
    r"(?P<amount>\d[\d\s.\u202f]{2,12}?)\s*(?:fcfa|xaf|cfa|frs|f\b)", re.IGNORECASE
)
GROUPED_AMOUNT_PATTERN = re.compile(r"(?<![\d.])(\d{1,3}(?:[ .\u202f]\d{3})+)(?![\d.])")
PLAIN_AMOUNT_PATTERN = re.compile(r"(?<![\d.])(\d{5,9})(?![\d.])")
# Local shorthand: in Cameroonian French "50 mil" is 50 000, but "mil" reads as
# "million" in most of a model's training data. The model is a bad place to
# settle a locale convention, so the meaning is owned here, in a rule.
SHORTHAND_MILLION_PATTERN = re.compile(
    r"(?<![\d.])(?P<amount>\d[\d\s.\u202f]{0,12}?)\s*(?:millions?|miyons?|mio)\b",
    re.IGNORECASE,
)
SHORTHAND_THOUSAND_PATTERN = re.compile(
    r"(?<![\d.])(?P<amount>\d[\d\s.\u202f]{0,12}?)\s*(?:milles?|mil|k)\b",
    re.IGNORECASE,
)
BEDROOM_PATTERN = re.compile(
    r"(?P<count>\d{1,2})\s*(?:chambres?|pieces?|pi[eè]ces?|bed\s?rooms?)", re.IGNORECASE
)
BATHROOM_PATTERN = re.compile(
    r"(?P<count>\d{1,2})\s*(?:salle[s]?\s+de\s+bain|salles?\s+d'eau|sdb|douches?)",
    re.IGNORECASE,
)
SURFACE_PATTERN = re.compile(r"(?P<area>\d{2,4})\s*m(?:2|²|sup)?(?![a-z])", re.IGNORECASE)
MODERN_PATTERN = re.compile(
    r"\b(moderne|modernes|neuf|neuve|recent|recente|standing)\b", re.IGNORECASE
)
NON_MODERN_PATTERN = re.compile(
    r"\b(non[\s-]?moderne|ancien|ancienne|vieux|vieille|vetuste|a refaire)\b", re.IGNORECASE
)

PROPERTY_TYPE_PATTERNS: dict[str, re.Pattern[str]] = {
    "APARTMENT": re.compile(r"\b(appartement|appart|apt|immeuble|appartement[s]?)\b", re.I),
    "STUDIO": re.compile(r"\b(studios?|sbg)\b", re.I),
    "HOUSE": re.compile(r"\b(maisons?|villas?|demeures?|townhouses?)\b", re.I),
    "DUPLEX": re.compile(r"\b(duplexs?|duplexe?s?)\b", re.I),
    "LOFT": re.compile(r"\b(lofts?)\b", re.I),
    "ROOM": re.compile(
        r"\b(chambres?\s+(?:meubl[ée]e?s?|a\s+louer|à\s+louer|d'etudiant|"
        r"d'[ée]tudiant|d'[ée]tage|partag[ée]e?s?)|chambres?\s+hote?s?)\b",
        re.I,
    ),
    "COMMERCIAL": re.compile(
        r"\b(boutiques?|commerces?|magasins?|bureaux?|locaux?\s+commerciaux?s?|"
        r"salons?\s+de\s+coiffure)\b",
        re.I,
    ),
    "LAND": re.compile(r"\b(terrains?|parcelles?|lots?|terrains?\s+a\s+b[aâ]tir)\b", re.I),
}

AVAILABILITY_PATTERNS: dict[str, re.Pattern[str]] = {
    "STILL_AVAILABLE": re.compile(
        r"\b(toujours\s+(?:disponible|libre|ok|dispo|valide)|c'est\s+toujours|"
        r"toujours\s+[aà]\s+louer|elle\s+est\s+toujours)\b",
        re.I,
    ),
    "RENTED": re.compile(
        r"\b(d[ée]j[àa]\s+lou[ée]|c'?est\s+lou[ée]|est\s+d[ée]j[àa]\s+lou[ée]|"
        r"[àa]\s+[ée]t[ée]\s+lou[ée]|j'?ai\s+lou[ée]|on\s+a\s+lou[ée]|"
        r"j'ai\s+trouv[ée]\s+(?:un\s+)?locataire|elle\s+est\s+lou[ée]|"
        r"c'est\s+d[ée]j[àa]\s+pris|parti\s+avec)\b",
        re.I,
    ),
    "TEMPORARILY_UNAVAILABLE": re.compile(
        r"\b(indisponibles?|pas\s+(?:dispo|disponible)|temporairement|"
        r"en\s+travaux?|parti[es]?\s+en\s+vacances?|sous\s+location\s+temporaire)\b",
        re.I,
    ),
}

ACQUISITION_SOURCE_PATTERNS: dict[str, re.Pattern[str]] = {
    "WHATSAPP": re.compile(r"\bwhats\s?app\b", re.I),
    "PLATFORM": re.compile(
        r"\b(sur\s+(?:la\s+|votre\s+l')?(?:plateforme|site|application|app)|"
        r"ici\s+sur\s+(?:votre|la)|depuis\s+(?:votre|l')application)\b",
        re.I,
    ),
    "FACEBOOK": re.compile(r"\b(facebook|f[bbc]\.com|messenger)\b", re.I),
    "TIKTOK": re.compile(r"\btik\s?tok\b", re.I),
    "INSTAGRAM": re.compile(r"\b(instagram|insta|ig)\b", re.I),
    "FRIEND": re.compile(r"\b(amis?|amie|connaissance|voisin|colocataire)\b", re.I),
    "FAMILY": re.compile(
        r"\b(famille|mon\s+p[èe]re|ma\s+m[èe]re|mon\s+fr[èe]re|ma\s+s[œoe]ur|"
        r"cousin[e]?|oncle|tante|parent[s]?)\b",
        re.I,
    ),
    "AGENCY": re.compile(r"\b(agence|agent\s+immobilier|courtier|notaire)\b", re.I),
    "WORD_OF_MOUTH": re.compile(
        r"\b(bouche[-\s]?à[-\s]?oreille|bouche[-\s]?a[-\s]?oreille|on\s+m'a\s+dit|"
        r"on\s+m'a\s+indiqu[ée]|quelqu'un\s+m'a)\b",
        re.I,
    ),
    "OTHER": re.compile(r"\b(autre|ailleurs|google|internet|publicit[ée])\b", re.I),
}

# Someone saying "je veux publier un appartement" has opened a listing, even
# though no price and no room count came with it. Without this the flow would
# sit in SUPPORT for ever, asking a landlord nothing about the flat they are
# trying to rent out.
LISTING_INTENT_PATTERN = re.compile(
    r"\b(je\s+(?:voudrais|souhaite|veux|dois|ai)\s+"
    r"(?:publier|mettre\s+en\s+ligne|vendre|louer|proposer)|"
    r"annonce[er]?|je\s+propose|disponible\s+a\s+louer)\b",
    re.I,
)

SEARCH_INTENT_PATTERN = re.compile(
    r"\b(je\s+(?:cherche|recherche|veux|voudrais)\s+(?:un|une|des)?|"
    r"avez[-\s]?vous\s+(?:un|une)|vous\s+avez\s+(?:un|une|des))\b",
    re.I,
)

LOCATION_HINT_PATTERN = re.compile(
    r"\b(douala|yaound[ée]|[ée]tat|garoua|bafoussam|buea|kribi|limbe|ngaoundéré|"
    r"bastos|bonapriso|akwa|makepe|mvan|bonamoussadi|logbessou|odza|nyalla)\b",
    re.I,
)


@dataclass(frozen=True, slots=True)
class DeterministicMatch:
    intent: str
    confidence: float
    entities: dict[str, object] = field(default_factory=dict)


class DeterministicClassifier:
    """Pure function from message text to a candidate intent."""

    def classify(self, text: str, *, pending: str | None = None) -> DeterministicMatch | None:
        normalized = self._normalize(text)
        if not normalized:
            return None

        availability = self._first_match(AVAILABILITY_PATTERNS, normalized)
        if availability:
            return DeterministicMatch(
                intent=f"AVAILABILITY_{availability}",
                confidence=0.95,
                entities={"availability": availability},
            )

        source = self._first_match(ACQUISITION_SOURCE_PATTERNS, normalized)
        if source:
            return DeterministicMatch(
                intent="COLLECT_ACQUISITION_SOURCE",
                confidence=0.93,
                entities={"acquisition_source": source},
            )

        entities = self._extract_entities(normalized)
        property_type = self._first_match(PROPERTY_TYPE_PATTERNS, normalized)
        if property_type:
            entities["property_type"] = property_type

        wants_to_list = bool(LISTING_INTENT_PATTERN.search(normalized))
        wants_to_search = bool(SEARCH_INTENT_PATTERN.search(normalized))

        # A tenant asking for a flat is never asking to publish one: the two
        # intents open different flows and confusing them would send a tenant
        # through a landlord's form.
        if wants_to_search and not wants_to_list:
            return DeterministicMatch(
                intent="PROPERTY_SEARCH", confidence=0.85, entities=entities
            )
        if property_type and (wants_to_list or "price" in entities or "bedrooms" in entities):
            return DeterministicMatch(
                intent="CREATE_PROPERTY", confidence=0.9, entities=entities
            )
        if property_type:
            return DeterministicMatch(
                intent="COLLECT_PROPERTY_TYPE", confidence=0.9, entities=entities
            )
        if "price" in entities:
            return DeterministicMatch(intent="COLLECT_PRICE", confidence=0.92, entities=entities)
        if {"bedrooms", "bathrooms", "surface_area"} & entities.keys():
            return DeterministicMatch(
                intent="COLLECT_FEATURES", confidence=0.9, entities=entities
            )

        standing = self._standing_reply(normalized, pending)
        if standing is not None:
            return standing
        if "standing" in entities:
            return DeterministicMatch(
                intent="COLLECT_STANDING", confidence=0.9, entities=entities
            )

        if normalized in AFFIRMATIVE:
            return DeterministicMatch(intent="AFFIRMATIVE", confidence=0.98)
        if normalized in NEGATIVE:
            return DeterministicMatch(intent="NEGATIVE", confidence=0.98)
        if normalized in GREETINGS:
            return DeterministicMatch(intent="SUPPORT", confidence=0.9)

        return None

    def shortcut(
        self, text: str, *, pending: str | None = None
    ) -> DeterministicMatch | None:
        """The unambiguous replies that must never cost a model call.

        Strictly narrower than :meth:`classify`: when a model is available the
        rules are only a fast lane, not the primary reader. An exact "oui"/"non"
        and a message whose only content is a price are the cases where the
        model can add nothing; "je cherche un appartement à Bastos" is not,
        because the intent (search vs listing) is exactly what the model is for.

        ``pending`` is the question the user is answering. It matters for
        "moderne ou non", where a bare "non" is a *value* for the field and not a
        refusal: without it the fast lane would re-ask the question.
        """
        normalized = self._normalize(text)
        if not normalized:
            return None
        standing = self._standing_reply(normalized, pending)
        if standing is not None:
            return standing
        if normalized in AFFIRMATIVE:
            return DeterministicMatch(intent="AFFIRMATIVE", confidence=0.98)
        if normalized in NEGATIVE:
            return DeterministicMatch(intent="NEGATIVE", confidence=0.98)
        entities = self._extract_entities(normalized)
        if "price" in entities and len(normalized.split()) <= 4:
            return DeterministicMatch(
                intent="COLLECT_PRICE",
                confidence=0.92,
                entities={"price": entities["price"]},
            )
        # Only when no question is open: mid-form a greeting may still come with
        # an answer the model should read together with the open question.
        if pending is None and normalized in GREETINGS:
            return DeterministicMatch(intent="SUPPORT", confidence=0.9)
        return None

    @staticmethod
    def _standing_reply(
        normalized: str, pending: str | None
    ) -> DeterministicMatch | None:
        """Read "oui"/"non" as MODERN/NON_MODERN when that is the open question."""
        if pending != "ask.standing":
            return None
        if normalized in AFFIRMATIVE:
            return DeterministicMatch(
                intent="COLLECT_STANDING",
                confidence=0.95,
                entities={"standing": "MODERN"},
            )
        if normalized in NEGATIVE:
            return DeterministicMatch(
                intent="COLLECT_STANDING",
                confidence=0.95,
                entities={"standing": "NON_MODERN"},
            )
        return None

    def shorthand_amount(self, text: str) -> int | None:
        """The amount when the message spells it as local shorthand, else None.

        Only the shorthand case is returned: it is exactly where the model's
        prior ("mil" = million) is wrong for this market, so the code owns the
        meaning and overrides the model's number. A plain "250 000" is left to
        the model, which reads it reliably.
        """
        normalized = self._normalize(text)
        for pattern, multiplier in (
            (SHORTHAND_MILLION_PATTERN, 1_000_000),
            (SHORTHAND_THOUSAND_PATTERN, 1_000),
        ):
            match = pattern.search(normalized)
            if match:
                value = _parse_amount(match.group("amount")) * multiplier
                if value >= MIN_PLAUSIBLE_RENT_XAF:
                    return value
        return None

    def to_intent_result(self, match: DeterministicMatch) -> IntentResult:
        return IntentResult(
            intent=match.intent,
            confidence=match.confidence,
            entities=dict(match.entities),
        )

    def _extract_entities(self, normalized: str) -> dict[str, object]:
        entities: dict[str, object] = {}
        raw_amount, multiplier = self._find_amount(normalized)
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
        location = LOCATION_HINT_PATTERN.search(normalized)
        if location:
            entities["location_hint"] = location.group(0).title()
        return entities

    @staticmethod
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

    @staticmethod
    def _first_match(patterns: dict[str, re.Pattern[str]], normalized: str) -> str | None:
        for label, pattern in patterns.items():
            if pattern.search(normalized):
                return label
        return None

    @staticmethod
    def _normalize(text: str) -> str:
        folded = (
            text.strip()
            .casefold()
            .replace("\u202f", " ")
            .replace("\u2019", "'")
        )
        return re.sub(r"\s+", " ", folded)


def _parse_amount(raw: str) -> int:
    digits = re.sub(r"[^\d]", "", raw)
    return int(digits) if digits else 0