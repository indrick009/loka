"""Candidates read by a model on OpenRouter, with what a match means here.

Only what idia already holds is sent: first name, age, sex, profession, city
and the private description, which idia alone reads. Any failure is ``None``
and the word-and-age order is used instead: nobody waits on a model.
"""

from __future__ import annotations

import json
import time
from typing import Any

import httpx

from loka.bounded_contexts.dating.domain.judge import Verdict
from loka.bounded_contexts.dating.domain.profile import Profile
from loka.shared.infrastructure.ai_usage import RecordUsage, tokens_of
from loka.shared.infrastructure.config.settings import AISettings
from loka.shared.infrastructure.logging import get_logger

_logger = get_logger(__name__)
_PROMPT = (
    "Tu es idia, une marieuse camerounaise. Une personne cherche l'amour ; classe "
    "les candidats du plus au moins compatible avec elle, en lisant DANS LES DEUX "
    "SENS : est-ce que le candidat correspond à ce qu'elle cherche, et est-ce qu'elle "
    "correspond à ce que le candidat cherche. Contexte camerounais : une femme "
    "préfère le plus souvent un homme plus âgé qu'elle (0 à 10 ans), sauf si sa "
    "description dit autre chose ; le sérieux (relation durable, mariage), la "
    "religion, les enfants, la ville et le mode de vie comptent beaucoup. Un red flag "
    "de l'un que l'autre présente (cigarette, alcool, mensonge, enfants…) fait chuter "
    "la note. Les préférences physiques dites (taille, poids) comptent aussi. "
    "Réponds en JSON : {\"ranking\": [{\"id\": int, \"score\": 0-100, \"reason\": "
    "une phrase courte}]} avec tous les candidats, le meilleur d'abord."
)


def _card(profile: Profile) -> str:
    return (
        f"{profile.first_name}, {profile.age} ans, {profile.sex.lower()}, "
        f"{profile.profession}, {profile.city} ({profile.country}). "
        f"Description : {profile.description}"
    )


class OpenRouterMatchJudge:
    def __init__(
        self, settings: AISettings, record: RecordUsage | None = None
    ) -> None:
        self._record = record
        self._settings = settings

    async def rank(self, member: Profile, candidates: list[Profile]) -> list[Verdict] | None:
        listing = "\n".join(f"{n}. {_card(p)}" for n, p in enumerate(candidates, 1))
        text = f"La personne : {_card(member)}\n\nLes candidats :\n{listing}"
        for model in dict.fromkeys((self._settings.default_model, self._settings.fallback_model)):
            payload = await self._ask(model, text)
            verdicts = _verdicts(payload, candidates) if payload is not None else None
            if verdicts:
                return verdicts
        return None

    async def _ask(self, model: str, text: str) -> dict[str, Any] | None:
        body = {
            "model": model,
            "temperature": 0,
            "max_tokens": 1200,
            "response_format": {"type": "json_object"},
            "messages": [
                {"role": "system", "content": _PROMPT},
                {"role": "user", "content": text},
            ],
        }
        headers = {"Authorization": f"Bearer {self._settings.api_key.get_secret_value()}"}
        try:
            async with httpx.AsyncClient(base_url=self._settings.base_url, timeout=25) as client:
                started = time.monotonic()
                response = await client.post("/chat/completions", json=body, headers=headers)
            response.raise_for_status()
            data = response.json()
            await self._usage(model, data, started)
            content = str(data["choices"][0]["message"]["content"]).strip()
            parsed = json.loads(content.strip("`").removeprefix("json").strip())
        except (httpx.HTTPError, KeyError, IndexError, TypeError, ValueError) as exc:
            _logger.warning("match_judge_failed", model=model, error=type(exc).__name__)
            return None
        return parsed if isinstance(parsed, dict) else None

    async def _usage(self, model: str, data: Any, started: float) -> None:
        if self._record is None:
            return
        try:
            tokens_in, tokens_out = tokens_of(data)
            elapsed = int((time.monotonic() - started) * 1000)
            await self._record("dating_match_judge", model, tokens_in, tokens_out, elapsed)
        except Exception as exc:  # a ledger failure never costs the answer
            _logger.warning("ai_usage_not_recorded", error=type(exc).__name__)


def _verdicts(payload: dict[str, Any], candidates: list[Profile]) -> list[Verdict] | None:
    seen: dict[int, Verdict] = {}
    for item in payload.get("ranking") or []:
        if not isinstance(item, dict):
            continue
        number, score = item.get("id"), item.get("score")
        if not isinstance(number, int) or not 1 <= number <= len(candidates) or number in seen:
            continue
        if not isinstance(score, int | float) or isinstance(score, bool):
            continue
        seen[number] = Verdict(
            profile_id=candidates[number - 1].profile_id,
            score=max(0.0, min(float(score), 100.0)) / 100,
            reason=str(item.get("reason") or "")[:200],
        )
    if not seen:
        return None
    # A candidate the model forgot keeps its place, after those it ranked.
    ranked = sorted(seen.values(), key=lambda v: v.score, reverse=True)
    named = {v.profile_id for v in ranked}
    return ranked + [
        Verdict(profile_id=p.profile_id, score=0.0, reason="") for p in candidates
        if p.profile_id not in named
    ]
