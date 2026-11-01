"""Reading a plugged domain's messages when no model can: the outage lane.

Observed in the replay of 1 Nov 2026 with an expired provider key: every
message — « je veux un billet », « j'ai perdu ma CNI », « je cherche l'âme
sœur » — fell to the housing rules and was asked « publier ou chercher ? ».
A worse reply beats no reply, but not a reply about the wrong service.

Two readings, both narrow on purpose:
- an opening, from the words each domain declares in ``DomainSpec.cues``;
- an answer to the field Kora just asked, when its shape is simple — an
  age, a count, a seat, a day, a name, a city, a yes or a no.
Anything else stays unread, and the user is asked to rephrase as before.
"""

from __future__ import annotations

import re
from datetime import date, datetime, timedelta, timezone
from typing import Any

from loka.bounded_contexts.conversation.application.plugs.registry import SPECS, spec_for
from loka.bounded_contexts.conversation.application.plugs.spec import DomainSpec
from loka.bounded_contexts.conversation.domain.entities.conversation_session import FlowName
from loka.shared.domain.knowledge import market

DOUALA = timezone(timedelta(hours=1))
# A lodging word means the housing rules own the message, whatever else it says.
_LODGING = re.compile(
    r"\b(chambres?|studios?|apparts?|appartements?|maisons?|villas?|loyer|louer|bailleur|duplex)\b"
)
_LEAD_IN = re.compile(
    r"^(?:je\s+m'?\s*appelle|moi\s+c'?\s*est|mon\s+(?:nom|pr[ée]nom)\s+(?:c'?\s*est|est)|c'?\s*est|"
    r"le\s+nom\s+c'?\s*est|je\s+suis(?:\s+une?)?|je\s+travaille\s+comme|my\s+name\s+(?:is|na)|i\s+be|i'?m|na)\s+",
    re.I,
)
_YES = {
    "oui",
    "ok",
    "dac",
    "d accord",
    "yes",
    "voila",
    "exactement",
    "correct",
    "oui oui",
    "ok ok",
    "na so",
}
_NO = {"non", "nn", "no", "non merci", "non non", "pas du tout", "lep"}
_SEX = {
    "HOMME": ("homme", "garcon", "gars", "man", "male", "monsieur"),
    "FEMME": ("femme", "fille", "go", "woman", "female", "dame"),
}
_LOOKING = {
    "QUARTIER": ("quartier", "kwatt", "pas loin", "quarter"),
    "VILLE": ("ville", "city"),
    "PAYS": ("pays", "cameroun", "mboa", "country"),
    "ETRANGER": ("etranger", "mbeng", "dehors", "outside", "diaspora"),
    "PEU_IMPORTE": ("peu importe", "n importe", "egal", "anywhere", "partout"),
}
_WEEKDAYS = ("lundi", "mardi", "mercredi", "jeudi", "vendredi", "samedi", "dimanche")
_EN_WEEKDAYS = ("monday", "tuesday", "wednesday", "thursday", "friday", "saturday", "sunday")
_PLACE_FIELDS = {"place", "location", "city"}
# « je veux aller à Maroua », « j vais a Garoua », « je descends sur Douala »: a
# travel verb followed by a known city is a trip, not a place to live.
_TRAVEL = re.compile(
    r"\b(?:aller|(?:je|j) (?:vais|pars|descends|monte|go|rentre))\s+"
    r"(?:a|au|sur|de|pour|vers|go)?\s*(.+)"
)
# Narrowest words first: « j'ai perdu ma CNI au restaurant » is Found, not Food.
_PRIORITY = ("found", "booking", "dating", "food")


def _norm(text: str) -> str:
    """Accents, case and punctuation folded: « J'ai perdu ma CNI ! » → « j ai perdu ma cni »."""
    return " ".join(re.sub(r"[^a-z0-9/ ]+", " ", market.normalize(text)).split())


_CITY_FIELDS = {"origin", "destination"}


def has_lodging_words(text: str) -> bool:
    return bool(_LODGING.search(_norm(text)))


def opening(text: str, *, now: datetime) -> tuple[str, dict[str, Any]] | None:
    """The plugged service a first message asks for, and the plain facts it already gives."""
    normalized = _norm(text)
    if not normalized or _LODGING.search(normalized):
        return None
    travel = _TRAVEL.search(normalized)
    if travel and _cities_in_order(travel.group(1)):
        booking = next((spec for spec in SPECS if spec.name == "booking"), None)
        if booking is not None:
            return "BOOKING_TRIP", sniff(booking, text, normalized, now=now)
    for spec in sorted(SPECS, key=lambda s: _PRIORITY.index(s.name) if s.name in _PRIORITY else 99):
        for intent, pattern in spec.cues.items():
            if re.search(pattern, normalized):
                return intent, sniff(spec, text, normalized, now=now)
    return None


def opening_intent(text: str) -> str | None:
    found = opening(text, now=datetime.now(DOUALA))
    return found[0] if found else None


def answer(
    flow: FlowName, pending: str | None, text: str, *, now: datetime
) -> tuple[str, dict[str, Any]] | None:
    """The intent and entities of a reply to the field just asked, if its shape is plain."""
    spec = spec_for(flow)
    if spec is None:
        return None
    normalized = _norm(text)
    if normalized in _YES:
        return "AFFIRMATIVE", {}
    if normalized in _NO:
        return "NEGATIVE", {}
    field = spec.field_of(pending)
    if field is None:
        return None
    digit = re.fullmatch(r"(?:le\s+|numero\s+|n\s*)?(\d)", normalized)
    if digit and field not in spec.entities:
        return "COLLECT_CHOICE", {"choice": int(digit.group(1))}
    entities = {
        **sniff(spec, text, normalized, now=now),
        **_read(spec, field, text, normalized, now=now),
    }
    if not entities:
        return None
    intent = next((i for i, f in spec.opening_intents.items() if f is flow), None)
    return (intent, entities) if intent else None


def _read(
    spec: DomainSpec, field: str, text: str, normalized: str, *, now: datetime
) -> dict[str, Any]:
    schema = spec.entities.get(field, {})
    kind = schema.get("type")
    numbers = [int(n) for n in re.findall(r"\b\d{1,3}\b", normalized)]
    if field == "travel_date":
        day = relative_day(normalized, now)
        return {"travel_date": day.isoformat()} if day else {}
    if field in _CITY_FIELDS:
        cleaned = re.sub(
            r"^(?:je\s+pars\s+de|je\s+vais\s+a|depuis|de|a|pour|vers|from|to)\s+", "", normalized
        )
        return {field: cleaned} if market.resolve_city(cleaned) else {}
    if field in _PLACE_FIELDS:
        return _place(normalized)
    if field == "first_name" and "," in text:
        head = text.split(",", 1)[0].strip()
        return {"first_name": _LEAD_IN.sub("", head, count=1).strip()} if head else {}
    if field == "document_type":
        kind = _document(normalized)
        return {"document_type": kind} if kind else {}
    if field == "sex":
        for sex, words in _SEX.items():
            if any(re.search(rf"\b{word}\b", normalized) for word in words):
                return {"sex": sex}
        return {}
    if field == "looking":
        picked = [key for key, words in _LOOKING.items() if any(w in normalized for w in words)]
        return {"looking": picked} if picked else {}
    if kind == "integer":
        return {field: numbers[0]} if numbers else {}
    if kind == "array" and schema.get("items", {}).get("type") == "integer":
        return {field: numbers} if numbers else {}
    if kind == "array":
        parts = [p.strip() for p in re.split(r",|\bet\b|\band\b", text) if p.strip()]
        return {field: parts} if parts else {}
    if kind == "string" or field in ("full_name", "first_name", "profession", "description"):
        raw = text.strip()
        cleaned = raw if field == "description" else _LEAD_IN.sub("", raw, count=1).strip(" .!")
        return {field: cleaned} if cleaned else {}
    return {}


_DOCUMENTS = (
    ("CNI", r"\b(cni|carte (nationale )?d identite|id card|national id|ma carte)\b"),
    ("PASSEPORT", r"\b(passeport|passport|pasport)\b"),
    ("PERMIS", r"\b(permis|driving licen[cs]e|licence)\b"),
    ("CARTE_ETUDIANT", r"\b(carte (d )?etudiante?|student card)\b"),
    ("AUTRE", r"\b(recepisse|carte grise|acte de naissance|diplome|releve|carte bancaire|badge)\b"),
)
_TRIP = re.compile(
    r"\b(?:de|depuis|from)\s+([a-z ]+?)\s+(?:a|pour|vers|to|go)\s+([a-z ]+?)"
    r"(?=\s+(?:demain|aujourd|apres|ce|le|pour|lundi|mardi|mercredi|jeudi|vendredi|"
    r"samedi|dimanche|tomorrow|today|\d)|$)"
)


_PLACE_LEAD = re.compile(
    r"^(?:je\s+suis\s+(?:a|au|vers)|je\s+vis\s+a|i\s+dey\s+for|du\s+cote\s+de|a|au|vers|for|in)\s+"
)


def _place(normalized: str) -> dict[str, Any]:
    """« carrefour obili », « carrefour hotel le paradis, yaounde », « vers mvan » → a place."""
    cleaned = _PLACE_LEAD.sub("", normalized)
    cities = _cities_in_order(cleaned)
    words = cleaned.split()
    for size in range(min(4, len(words)), 0, -1):
        for start in range(len(words) - size + 1):
            candidate = " ".join(words[start : start + size])
            if candidate != "carrefour" and market.resolve_neighbourhood(candidate):
                return {"neighbourhood": candidate, **({"city": cities[0]} if cities else {})}
    if cities:
        rest = " ".join(w for w in words if w not in cities[0].split())
        return {"city": cities[0], **({"neighbourhood": rest} if rest else {})}
    return {}


def _document(normalized: str) -> str | None:
    return next((kind for kind, pattern in _DOCUMENTS if re.search(pattern, normalized)), None)


def _city_in(fragment: str) -> str | None:
    """The longest known city at the start of a fragment: « bafoussam demain » → Bafoussam."""
    words = fragment.split()
    for size in range(min(3, len(words)), 0, -1):
        candidate = " ".join(words[:size])
        if market.resolve_city(candidate):
            return candidate
    return None


def _cities_in_order(normalized: str) -> list[str]:
    words, seen, i = normalized.split(), [], 0
    while i < len(words):
        city = _city_in(" ".join(words[i : i + 3]))
        if city:
            seen.append(city)
            i += len(city.split())
        else:
            i += 1
    return seen


def sniff(spec: DomainSpec, text: str, normalized: str, *, now: datetime) -> dict[str, Any]:
    """Every plain fact a message states, whatever was asked: « Ndeye, 32 ans, femme »."""
    names = set(spec.entities) | {item.name for flow in spec.flows for item in spec.form(flow)}
    found: dict[str, Any] = {}
    if "age" in names and (m := re.search(r"\b(\d{2})\s*(?:ans|years?|yrs)\b", normalized)):
        found["age"] = int(m.group(1))
    if "sex" in names:
        for sex, pattern in (
            ("HOMME", r"\b(homme|garcon|un gars|man|male)\b"),
            ("FEMME", r"\b(femme|fille|une go|woman|female)\b"),
        ):
            if re.search(pattern, normalized):
                found["sex"] = sex
                break
    if "city" in names and (
        m := re.search(
            r"\b(?:je vis|j vis|j habite|i live|i dey)\s+(?:a|au|en|in|for)\s+([a-z ]+)", normalized
        )
    ):
        city = _city_in(m.group(1)) or m.group(1).split(" et ")[0].strip()
        found["city"] = city
    if "first_name" in names:
        m = re.search(r"\b(?:je m appelle|moi c est|my name (?:is|na))\s+([a-z]+)", normalized)
        head = text.split(",", 1)[0].strip() if "," in text else ""
        if m:
            found["first_name"] = m.group(1).title()
        elif head and re.fullmatch(r"[A-Za-zÀ-ÿ'-]{2,20}", head):
            found["first_name"] = head[0].upper() + head[1:]
    if "document_type" in names and (kind := _document(normalized)):
        found["document_type"] = kind
    if {"origin", "destination"} <= names:
        if m := _TRIP.search(normalized):
            origin, destination = _city_in(m.group(1)), _city_in(m.group(2))
            found |= {k: v for k, v in (("origin", origin), ("destination", destination)) if v}
        if m := re.search(r"\bje pars de\s+([a-z ]+)", normalized):
            found.setdefault("origin", _city_in(m.group(1)))
        if m := re.search(r"\b(?:je vais a|je descends a|je go a|pour|go)\s+([a-z ]+)", normalized):
            found.setdefault("destination", _city_in(m.group(1)))
        if not found.get("origin") and not found.get("destination"):
            # « un billet Douala Yaoundé » : two cities in a row, in travel order.
            cities = _cities_in_order(normalized)
            if len(cities) == 2:
                found["origin"], found["destination"] = cities
        found = {k: v for k, v in found.items() if v}
    if "travel_date" in names and (day := relative_day(normalized, now)):
        found["travel_date"] = day.isoformat()
    if "passengers" in names and (
        m := re.search(
            r"\b(\d)\s*(?:places?|personnes?|seats?)\b|\bnous sommes\s+(\d)\b|\bwe be\s+(\d)\b",
            normalized,
        )
    ):
        found["passengers"] = int(next(g for g in m.groups() if g))
    return found


def relative_day(normalized: str, now: datetime) -> date | None:
    """« demain », « samedi », « 15/11 » → a date, in Douala time."""
    today = now.astimezone(DOUALA).date()
    if re.search(r"\b(apres[- ]?demain|apre dmain|day after)\b", normalized):
        return today + timedelta(days=2)
    if re.search(r"\b(demain|dmain|tomorrow|tumoro)\b", normalized):
        return today + timedelta(days=1)
    if re.search(r"\b(aujourd ?hui|ajd|today|tiday|ce soir|maintenant)\b", normalized):
        return today
    for names in (_WEEKDAYS, _EN_WEEKDAYS):
        for index, name in enumerate(names):
            if re.search(rf"\b{name}\b", normalized):
                return today + timedelta(days=(index - today.weekday()) % 7 or 7)
    match = re.search(r"\b(\d{1,2})/(\d{1,2})\b", normalized)
    if match:
        day, month = int(match.group(1)), int(match.group(2))
        try:
            candidate = date(today.year, month, day)
        except ValueError:
            return None
        return candidate if candidate >= today else date(today.year + 1, month, day)
    return None
