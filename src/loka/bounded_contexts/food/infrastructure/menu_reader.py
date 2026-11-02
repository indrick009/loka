"""Reads a restaurateur's photo with a vision model on OpenRouter.

The image is sent inline (base64): the bucket is not reachable from the
provider. Any failure is ``None`` — the photo is then kept as a dish photo, and
nobody is left without an answer because a model was down.
"""

from __future__ import annotations

import base64
import json
from typing import Any

import httpx

from loka.bounded_contexts.food.domain.reader import MenuReading
from loka.shared.infrastructure.config.settings import AISettings
from loka.shared.infrastructure.logging import get_logger
from loka.shared.infrastructure.object_storage.media import MediaStoreError, S3MediaStorage

_logger = get_logger(__name__)
_MAX_BYTES = 6_000_000
_PROMPT = (
    "Tu regardes une photo envoyée par un restaurant au Cameroun. Réponds en JSON : "
    '{"kind": "MENU" | "DISH" | "OTHER", "lines": [{"name": str, "price": int | null}], '
    '"dish": str | null}. MENU = un menu écrit (tableau, ardoise, feuille) : recopie '
    "chaque plat dans 'lines' avec son prix en FCFA tel qu'écrit ('2 mil', '2000f', "
    "'2.000' = 2000 ; un nombre seul comme '2' ou '2,5' à côté d'un plat = milliers), "
    "null si aucun prix. DISH = la photo d'un plat servi : 'dish' = son nom local si "
    "tu le reconnais (ndolè, eru, poulet DG, poisson braisé, soya, okok, koki, achu...), "
    "sinon null. OTHER = autre chose. N'invente aucun plat ni prix."
)


class OpenRouterMenuReader:
    def __init__(self, settings: AISettings, storage: S3MediaStorage) -> None:
        self._settings = settings
        self._storage = storage

    async def read(self, object_key: str) -> MenuReading | None:
        try:
            data, content_type = await self._storage.get(object_key)
        except MediaStoreError as exc:
            _logger.warning("menu_photo_unreadable", error=str(exc))
            return None
        if len(data) > _MAX_BYTES or not content_type.startswith("image/"):
            return None
        image = f"data:{content_type};base64,{base64.b64encode(data).decode()}"
        for model in dict.fromkeys((self._settings.default_model, self._settings.fallback_model)):
            payload = await self._ask(model, image)
            if payload is not None:
                return _reading(payload)
        return None

    async def _ask(self, model: str, image: str) -> dict[str, Any] | None:
        body = {
            "model": model,
            "temperature": 0,
            "max_tokens": 1500,
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
            async with httpx.AsyncClient(base_url=self._settings.base_url, timeout=40) as client:
                response = await client.post("/chat/completions", json=body, headers=headers)
            response.raise_for_status()
            content = str(response.json()["choices"][0]["message"]["content"]).strip()
            parsed = json.loads(content.strip("`").removeprefix("json").strip())
        except (httpx.HTTPError, KeyError, IndexError, TypeError, ValueError) as exc:
            _logger.warning("menu_reader_failed", model=model, error=type(exc).__name__)
            return None
        return parsed if isinstance(parsed, dict) else None


def _reading(payload: dict[str, Any]) -> MenuReading:
    kind = payload.get("kind")
    lines: list[tuple[str, int | None]] = []
    for item in payload.get("lines") or []:
        if not isinstance(item, dict) or not str(item.get("name") or "").strip():
            continue
        price = item.get("price")
        valid = isinstance(price, int) and not isinstance(price, bool)
        lines.append((str(item["name"]).strip(), price if valid else None))
    dish = payload.get("dish")
    return MenuReading(
        kind=kind if kind in ("MENU", "DISH") else "OTHER",
        lines=lines,
        dish=dish.strip() if isinstance(dish, str) and dish.strip() else None,
    )
