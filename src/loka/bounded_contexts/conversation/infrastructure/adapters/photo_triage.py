"""What a photo shows, read by a vision model on OpenRouter.

Asked only of a photo sent with no task in progress: Nsai's two dishes were
taken for a lodging to rent (journal of 25 Nov 2026, 09:44). Any failure
answers ``None``, and idia asks a neutral question instead.
"""

from __future__ import annotations

import base64
import json

import httpx

from loka.bounded_contexts.conversation.application.ports import PHOTO_SUBJECTS
from loka.shared.infrastructure.config.settings import AISettings
from loka.shared.infrastructure.logging import get_logger
from loka.shared.infrastructure.object_storage.media import MediaStoreError, S3MediaStorage

_logger = get_logger(__name__)
_MAX_BYTES = 6_000_000
_PROMPT = (
    "Une personne au Cameroun envoie cette photo à un assistant WhatsApp, sans rien dire. "
    "Que montre-t-elle ? Réponds en JSON : "
    '{"subject": "FOOD"|"MENU"|"LODGING"|"DOCUMENT"|"PORTRAIT"|"OTHER"}. '
    "FOOD = un plat, de la nourriture, une boisson. MENU = un menu ou une liste de plats "
    "écrite ou imprimée. LODGING = une maison, un appartement, une chambre, un studio, une "
    "pièce, une façade. DOCUMENT = une pièce d'identité, un passeport, un permis, une carte "
    "d'étudiant, un acte ou un diplôme. PORTRAIT = une personne, un selfie. OTHER = autre "
    "chose, ou dans le doute."
)


class OpenRouterPhotoTriage:
    def __init__(self, settings: AISettings, storage: S3MediaStorage) -> None:
        self._settings = settings
        self._storage = storage

    async def subject(self, object_key: str) -> str | None:
        try:
            data, content_type = await self._storage.get(object_key)
        except MediaStoreError as exc:
            _logger.warning("photo_triage_unreadable", error=str(exc))
            return None
        if len(data) > _MAX_BYTES or not content_type.startswith("image/"):
            return None
        image = f"data:{content_type};base64,{base64.b64encode(data).decode()}"
        for model in dict.fromkeys((self._settings.default_model, self._settings.fallback_model)):
            subject = await self._ask(model, image)
            if subject is not None:
                _logger.info("photo_triaged", subject=subject)
                return subject
        return None

    async def _ask(self, model: str, image: str) -> str | None:
        body = {
            "model": model,
            "temperature": 0,
            "max_tokens": 50,
            "response_format": {"type": "json_object"},
            "messages": [
                {
                    "role": "user",
                    "content": [
                        {"type": "text", "text": _PROMPT},
                        {"type": "image_url", "image_url": {"url": image}},
                    ],
                }
            ],
        }
        headers = {"Authorization": f"Bearer {self._settings.api_key.get_secret_value()}"}
        try:
            async with httpx.AsyncClient(base_url=self._settings.base_url, timeout=20) as client:
                response = await client.post("/chat/completions", json=body, headers=headers)
            response.raise_for_status()
            content = str(response.json()["choices"][0]["message"]["content"]).strip()
            parsed = json.loads(content.strip("`").removeprefix("json").strip())
        except (httpx.HTTPError, KeyError, IndexError, TypeError, ValueError) as exc:
            _logger.warning("photo_triage_failed", model=model, error=type(exc).__name__)
            return None
        subject = parsed.get("subject") if isinstance(parsed, dict) else None
        return subject if subject in PHOTO_SUBJECTS else None
