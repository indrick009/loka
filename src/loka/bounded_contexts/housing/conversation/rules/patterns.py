"""Patterns for amounts, rooms, standing, lodging types and intents."""

from __future__ import annotations

import re

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


# Order matters: ``_first_match`` returns the first hit, and "un studio a été
# pris" must not be read as "tout a été pris". One decrements and leaves the
# listing published; the other takes it out of search, and a landlord with two
# studios would lose the second one by letting the first go.
AVAILABILITY_PATTERNS: dict[str, re.Pattern[str]] = {
    "ONE_TAKEN": re.compile(
        r"\b(?:il\s+y\s+en\s+a\s+)?(?:un|une|1)\s+"
        r"(?:studio|chambre|appartement|appart|maison|logement|seul|seule)?\s*"
        r"(?:a|est|ont)?\s*(?:[ée]t[ée]\s+)?(?:pris|prise|lou[ée]e?|parti[e]?)\b"
        r"|\bj\'?ai\s+lou[ée]\s+(?:un|une|1)\b",
        re.I,
    ),
    "STILL_AVAILABLE": re.compile(
        r"\b(toujours\s+(?:disponible|libre|ok|dispo|valide)|c'est\s+toujours|"
        r"toujours\s+[aà]\s+louer|elle\s+est\s+toujours)\b",
        re.I,
    ),
    "RENTED": re.compile(
        r"\b(d[ée]j[àa]\s+lou[ée]|c'?est\s+lou[ée]|est\s+d[ée]j[àa]\s+lou[ée]|"
        r"[àa]\s+[ée]t[ée]\s+lou[ée]|j'?ai\s+lou[ée]|on\s+a\s+lou[ée]|"
        r"j'ai\s+trouv[ée]\s+(?:un\s+)?locataire|elle\s+est\s+lou[ée]|"
        r"c'est\s+d[ée]j[àa]\s+pris|parti\s+avec|"
        r"tout\s+(?:a\s+|est\s+)?(?:[ée]t[ée]\s+)?(?:pris|lou[ée])|"
        r"tous?\s+(?:ont\s+)?(?:[ée]t[ée]\s+)?(?:pris|lou[ée]s?)|"
        r"plus\s+rien|il\s+n'?y\s+a\s+plus)\b",
        re.I,
    ),
    "TEMPORARILY_UNAVAILABLE": re.compile(
        r"\b(indisponibles?|pas\s+(?:dispo|disponible)|temporairement|"
        r"en\s+travaux?|parti[es]?\s+en\s+vacances?|sous\s+location\s+temporaire)\b",
        re.I,
    ),
}


# "Logbessou aussi" adds a place to the ones already accepted; read as a plain
# location it replaced them. Exported because the classifier cannot see the
# known facts and the use case, which can, does the union.
ADDS_NEIGHBOURHOODS = "adds_neighbourhoods"


_ADDS_A_PLACE = re.compile(r"\b(aussi|egalement|également|en plus|also|as well)\b", re.I)


# No step asks this question yet, so the patterns below lie dormant until one
# does; they are kept because the answers they read are the ones to expect.
_ACQUISITION_QUESTION = "ask.acquisition_source"


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
#
# ``louer`` is deliberately NOT one of the verbs here. "Je veux louer un studio"
# is a *tenant* looking for a home, and a tenant sent through a landlord's form
# is the role mix-up this pipeline exists to avoid. A landlord renting out their
# own place owns it, and the possessive in ``_LANDLORD_OWNERSHIP_PATTERN`` is
# what tells the two apart.
LISTING_INTENT_PATTERN = re.compile(
    r"\b(je\s+(?:voudrais|souhaite|veux|dois|ai)\s+"
    r"(?:publier|mettre\s+en\s+ligne|vendre|proposer)|"
    # "je veux le mettre sur Kora" — the pronoun is the lodging he has just
    # named; read as "je veux ..." it sent a landlord to the tenant's form.
    r"je\s+(?:voudrais|souhaite|veux)\s+(?:le|la|les)\s+(?:mettre|publier|proposer)|"
    r"annonce[er]?|je\s+propose|disponible\s+[aà]\s+louer)\b",
    re.I,
)


# "Je louer mon studio" is a landlord naming the home they own. The possessive
# before a property noun is the one unambiguous marker of ownership in a phrase
# where "louer" alone reads as a tenant ("je loue un studio").
# "J'ai un studio à louer" says it as plainly as "mon studio": having one is
# owning one. A tenant "a besoin d'un studio"; he does not "have" one.
_LANDLORD_OWNERSHIP_PATTERN = re.compile(
    r"\b(?:mon|ma|mes|j\s*'?\s*ai\s+(?:un|une|des|deux|trois|\d+))\s+"
    r"(?:appartements?|apparts?|studios?|maisons?|villas?|"
    r"chambres?|duplex(?:e?s?)?|lofts?|locaux|bureaux?|immeubles?|terrains?|"
    r"parcelles?|pi[eè]ces?|sbg)\b",
    re.I,
)


SEARCH_INTENT_PATTERN = re.compile(
    r"\b(je\s+(?:cherche|recherche|veux|voudrais|wanda)\s+(?:un|une|des)?|"
    r"avez[-\s]?vous\s+(?:un|une)|vous\s+avez\s+(?:un|une|des)|"
    # How Cameroonians ask on WhatsApp (replay of 1 Nov 2026): « besoin d'un
    # studio », « on m'a dit que vous trouvez les appartements », « I di find
    # room », « una get self contain? ».
    r"(?:j'?\s*ai\s+)?besoin\s+d'?\s*(?:un|une)|il\s+me\s+faut|aidez[-\s]?moi\s+[àa]\s+trouver|"
    r"trouve[rz]?[-\s]?moi|vous\s+trouvez|je\s+dois\s+long|"
    r"i\s+di\s+find|i\s+(?:need|want)\s+(?:a|one)?|looking\s+for|do\s+you\s+have|una\s+get)\b",
    re.I,
)


LOCATION_HINT_PATTERN = re.compile(
    r"\b(douala|yaound[ée]|[ée]tat|garoua|bafoussam|buea|kribi|limbe|ngaoundéré|"
    r"bastos|bonapriso|akwa|makepe|mvan|bonamoussadi|logbessou|odza|nyalla)\b",
    re.I,
)
