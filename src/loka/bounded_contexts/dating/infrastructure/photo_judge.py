"""A dating photo looked at by a vision model on OpenRouter.

Sent inline (base64), as the menu reader does. Any failure accepts the photo
and is logged: an outage must not stop every profile at the photo step.
"""

from __future__ import annotations

import base64
import json
import time
from typing import Any

import httpx

from loka.bounded_contexts.dating.domain.photo_check import REASONS, PhotoVerdict
from loka.shared.infrastructure.ai_usage import RecordUsage, tokens_of
from loka.shared.infrastructure.config.settings import AISettings
from loka.shared.infrastructure.logging import get_logger
from loka.shared.infrastructure.object_storage.media import MediaStoreError, S3MediaStorage

_logger = get_logger(__name__)
_MAX_BYTES = 6_000_000
_PROMPT = (
    "Tu vérifies la photo de profil d'un service de rencontre au Cameroun. Elle doit être "
    "une vraie photo de la personne elle-même : un seul adulte, visage bien visible, photo "
    "prise par elle ou un proche (selfie, photo de téléphone). Réponds en JSON : "
    '{"ok": true|false, "reason": "AI"|"STOCK"|"CELEBRITY"|"NO_FACE"|"GROUP"|"CHILD"|'
    '"NOT_PHOTO"|"OTHER_SEX"|null}. AI = visage généré par IA (portrait carré '
    "parfaitement centré, peau "
    "trop lisse, arrière-plan flou ou déformé, artefacts, taches, oreilles ou cheveux "
    "incohérents, style thispersondoesnotexist) ou photo fortement retouchée par un filtre. "
    "STOCK = photo de banque d'images, de publicité ou de mannequin professionnel. "
    "CELEBRITY = personne célèbre. NO_FACE = pas de visage net. GROUP = plusieurs personnes "
    "sans savoir laquelle. CHILD = un enfant. NOT_PHOTO = dessin, capture d'écran, objet. "
    "Sans visage humain net et reconnaissable (paysage, objet, animal, fleur, dos, "
    "silhouette lointaine, image quelconque), ok = false avec NO_FACE, même dans le "
    "doute. Dans le doute seulement sur une photo de téléphone ordinaire montrant "
    "bien un visage, ok = true."
)
_SEX = {
    "HOMME": "La personne dit être un homme",
    "FEMME": "La personne dit être une femme",
}
_SEX_RULE = (
    "{said} : si la photo montre clairement une personne de l'autre sexe, ok = false "
    "avec OTHER_SEX."
)


class OpenRouterPhotoJudge:
    def __init__(
        self, settings: AISettings, storage: S3MediaStorage, record: RecordUsage | None = None
    ) -> None:
        self._record = record
        self._settings = settings
        self._storage = storage

    async def check(self, object_key: str, *, sex: str | None = None) -> PhotoVerdict:
        try:
            data, content_type = await self._storage.get(object_key)
        except MediaStoreError as exc:
            _logger.warning("dating_photo_unreadable", error=str(exc))
            return PhotoVerdict(ok=True)
        if len(data) > _MAX_BYTES or not content_type.startswith("image/"):
            return PhotoVerdict(ok=True)
        image = f"data:{content_type};base64,{base64.b64encode(data).decode()}"
        for model in dict.fromkeys((self._settings.default_model, self._settings.fallback_model)):
            payload = await self._ask(model, image, sex)
            if payload is not None and isinstance(payload.get("ok"), bool):
                reason = payload.get("reason")
                verdict = PhotoVerdict(
                    ok=payload["ok"], reason=reason if reason in REASONS else None
                )
                _logger.info("dating_photo_checked", ok=verdict.ok, reason=verdict.reason)
                return verdict
        _logger.warning("dating_photo_check_unavailable")
        return PhotoVerdict(ok=True)

    async def _ask(self, model: str, image: str, sex: str | None) -> dict[str, Any] | None:
        prompt = f"{_PROMPT} {_SEX_RULE.format(said=_SEX[sex])}" if sex in _SEX else _PROMPT
        body = {
            "model": model,
            "temperature": 0,
            "max_tokens": 200,
            "response_format": {"type": "json_object"},
            "messages": [
                {
                    "role": "user",
                    "content": [
                        {"type": "text", "text": prompt},
                        {"type": "image_url", "image_url": {"url": image}},
                    ],
                }
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
            _logger.warning("dating_photo_judge_failed", model=model, error=type(exc).__name__)
            return None
        return parsed if isinstance(parsed, dict) else None

    async def _usage(self, model: str, data: Any, started: float) -> None:
        if self._record is None:
            return
        try:
            tokens_in, tokens_out = tokens_of(data)
            elapsed = int((time.monotonic() - started) * 1000)
            await self._record("dating_photo_check", model, tokens_in, tokens_out, elapsed)
        except Exception as exc:  # a ledger failure never costs the answer
            _logger.warning("ai_usage_not_recorded", error=type(exc).__name__)
