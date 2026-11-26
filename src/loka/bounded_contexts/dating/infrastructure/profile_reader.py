"""A dating profile read by a model into traits and wishes (see ``attributes``).

The model is given the vocabulary — the base keys and every key members
already brought — and may add one when a member speaks of something it does
not cover. Any failure answers ``None``: the profile is ranked without.
"""

from __future__ import annotations

import json
import time
from typing import Any

import httpx

from loka.bounded_contexts.dating.domain.attributes import MUST, PREFER, Wish, key_of
from loka.bounded_contexts.dating.domain.profile import Profile
from loka.bounded_contexts.dating.domain.profile_reader import ProfileReading
from loka.shared.infrastructure.ai_usage import RecordUsage, tokens_of
from loka.shared.infrastructure.config.settings import AISettings
from loka.shared.infrastructure.logging import get_logger

_logger = get_logger(__name__)
_MAX_TRAITS = 20
_MAX_WISHES = 15
_PROMPT = (
    "Tu lis le profil de rencontre d'une personne au Cameroun et tu en tires deux "
    "listes, en JSON.\n"
    "1. traits : ce que la personne EST, d'après ce qu'elle dit d'elle-même "
    "(clé → valeur courte). N'invente rien : une clé non dite est absente.\n"
    "2. wishes : ce qu'elle VEUT ou ÉVITE chez l'autre. Chaque souhait : key, "
    "values (mots à retrouver chez l'autre) ou low/high (nombres, pour l'âge en "
    "années ou la taille en cm), wants (true = veut, false = évite), strength : "
    f"\"{MUST}\" seulement si c'est absolu (« jamais », « surtout pas », "
    f"« uniquement », « obligatoirement »), sinon \"{PREFER}\". « Avec ou sans "
    "enfant », « peu importe » : aucun souhait sur cette clé. « Plus de 26 ans » : "
    "key age, low 26. « Pas chauffeur de taxi » : key metier, values [\"taxi\"], "
    "wants false.\n"
    "Utilise ces clés quand elles conviennent :\n{keys}\n"
    "Si la personne parle d'autre chose qui compte pour elle (sport, tatouages, "
    "langue…), crée une clé courte en minuscules sans accent (ex. « sport »), la "
    "même pour les traits et les souhaits.\n"
    'Réponds : {{"traits": {{"clé": "valeur"}}, "wishes": [{{"key": "...", '
    '"values": [], "low": null, "high": null, "wants": true, "strength": "prefer"}}]}}'
)


def _card(profile: Profile) -> str:
    return (
        f"{profile.first_name}, {profile.age} ans, {profile.sex.lower()}, "
        f"{profile.profession}, {profile.city} ({profile.country}).\n"
        f"Ce qu'elle a écrit : {profile.description}"
    )


class OpenRouterProfileReader:
    def __init__(self, settings: AISettings, record: RecordUsage | None = None) -> None:
        self._settings = settings
        self._record = record

    async def read(self, profile: Profile, keys: dict[str, str]) -> ProfileReading | None:
        listed = "\n".join(f"- {key} : {meaning}" for key, meaning in sorted(keys.items()))
        system = _PROMPT.format(keys=listed)
        for model in dict.fromkeys((self._settings.default_model, self._settings.fallback_model)):
            payload = await self._ask(model, system, _card(profile))
            if payload is not None:
                reading = _reading(payload)
                _logger.info(
                    "dating_profile_read", traits=len(reading.traits), wishes=len(reading.wishes)
                )
                return reading
        return None

    async def _ask(self, model: str, system: str, user: str) -> dict[str, Any] | None:
        body = {
            "model": model,
            "temperature": 0,
            "max_tokens": 900,
            "response_format": {"type": "json_object"},
            "messages": [
                {"role": "system", "content": system},
                {"role": "user", "content": user},
            ],
        }
        headers = {"Authorization": f"Bearer {self._settings.api_key.get_secret_value()}"}
        try:
            async with httpx.AsyncClient(base_url=self._settings.base_url, timeout=30) as client:
                started = time.monotonic()
                response = await client.post("/chat/completions", json=body, headers=headers)
            response.raise_for_status()
            data = response.json()
            await self._usage(model, data, started)
            content = str(data["choices"][0]["message"]["content"]).strip()
            parsed = json.loads(content.strip("`").removeprefix("json").strip())
        except (httpx.HTTPError, KeyError, IndexError, TypeError, ValueError) as exc:
            _logger.warning("dating_profile_reader_failed", model=model, error=type(exc).__name__)
            return None
        return parsed if isinstance(parsed, dict) else None

    async def _usage(self, model: str, data: Any, started: float) -> None:
        if self._record is None:
            return
        try:
            tokens_in, tokens_out = tokens_of(data)
            elapsed = int((time.monotonic() - started) * 1000)
            await self._record("dating_profile_reader", model, tokens_in, tokens_out, elapsed)
        except Exception as exc:  # a ledger failure never costs the reading
            _logger.warning("ai_usage_not_recorded", error=type(exc).__name__)


def _reading(payload: dict[str, Any]) -> ProfileReading:
    raw_traits = payload.get("traits")
    traits: dict[str, str] = {}
    if isinstance(raw_traits, dict):
        for key, value in list(raw_traits.items())[:_MAX_TRAITS]:
            name, said = key_of(key), " ".join(str(value or "").split())[:80]
            if name and said:
                traits[name] = said
    raw_wishes = payload.get("wishes")
    wishes = tuple(
        wish
        for wish in (
            Wish.from_dict(raw)
            for raw in (raw_wishes if isinstance(raw_wishes, list) else [])[:_MAX_WISHES]
            if isinstance(raw, dict)
        )
        if wish is not None and (wish.values or wish.low is not None or wish.high is not None)
    )
    return ProfileReading(traits=traits, wishes=wishes)
