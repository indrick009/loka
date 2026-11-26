"""Reads a restaurateur's photo with a vision model on OpenRouter.

The image is sent inline (base64): the bucket is not reachable from the
provider. Any failure is ``None`` — the photo is then kept as a dish photo, and
nobody is left without an answer because a model was down.
"""

from __future__ import annotations

import base64
import json
import time
from typing import Any

import httpx

from loka.bounded_contexts.food.domain.reader import MenuReading
from loka.shared.infrastructure.ai_usage import RecordUsage, tokens_of
from loka.shared.infrastructure.config.settings import AISettings
from loka.shared.infrastructure.logging import get_logger
from loka.shared.infrastructure.object_storage.media import MediaStoreError, S3MediaStorage

_logger = get_logger(__name__)
_MAX_BYTES = 6_000_000
_PROMPT = (
    "Tu regardes une photo envoyée par un restaurant au Cameroun. Réponds en JSON : "
    '{"kind": "MENU" | "DISH" | "MIXED" | "OTHER", "lines": [{"name": str, "price": int | null}], '
    '"dish": str | null}. MENU = un menu écrit (tableau, ardoise, feuille, '
    "page de menu ou affiche où chaque plat a son nom et son prix, même avec ses "
    "photos) : recopie chaque plat dans 'lines' avec son prix en FCFA tel qu'écrit "
    "('2 mil', '2000f', "
    "'2.000' = 2000 ; un nombre seul comme '2' ou '2,5' à côté d'un plat = milliers), "
    "null si aucun prix. DISH = la photo d'un plat servi : 'dish' = son nom local si "
    "tu le reconnais (ndolè, eru, poulet DG, poisson braisé, soya, okok, koki, achu...), "
    "sinon null. MIXED = plusieurs plats ou produits différents sur la même photo "
    "(un étalage, une table garnie, plusieurs pots ou assiettes différents), sans menu "
    "écrit. OTHER = autre chose. N'invente aucun plat ni prix.\n"
    "Pour DISH, ajoute aussi : 'price' = le prix donné par la légende du restaurateur "
    "s'il y en a une (« 1000f », « 1000g », « 1 000 », « 1500 frs », « 2 mil » ; un "
    "nombre seul sous 100 = milliers), sinon null ; et 'description' = une phrase "
    "courte et appétissante de ce qu'on voit dans l'assiette (ingrédients, "
    "accompagnement, quantité), sans inventer ce qu'on ne voit pas. Quand la légende "
    "nomme le plat, 'dish' est ce nom-là, tel qu'écrit, corrigé des fautes de frappe."
)


# Whether a menu line needs its accompaniment said (decision of 26 Nov 2026):
# « Fried rice au poulet » was asked « avec quoi le sers-tu ? ».
_SIDES_PROMPT = (
    "Tu connais la cuisine camerounaise (ndolè, eru, okok, koki, achu, mbongo, nkui, "
    "kondrè, sanga, ekwang, kwacoco, banga soup, corn chaff, kati kati, poulet DG, soya, "
    "fried rice, jollof, riz sauté, omelette spaghetti, bouillons, brochettes, poisson ou "
    "poulet braisé…). Pour chaque plat de ce menu, dis s'il manque l'accompagnement : "
    "seulement quand le nom désigne une sauce ou une viande qu'on mange avec autre chose "
    "(plantain, bâton de manioc, riz, couscous, water fufu, frites…) et que le nom ne le "
    "dit pas. Un plat complet en lui-même (fried rice, jollof, poulet DG, riz sauté, "
    "spaghetti, bouillon, soupe, soya, salade, beignets-haricots), un accompagnement vendu "
    "seul, une boisson, un dessert ou un produit vendu cru ou au kilo n'en manque pas. Si "
    "le menu liste ses accompagnements à part, aucun plat n'en manque. Réponds en JSON : "
    '{"lacking": [{"name": "nom exact du plat", "sides": ["2 à 4 accompagnements '
    'habituels"]}]}, une liste vide si aucun.'
)


class OpenRouterMenuReader:
    def __init__(
        self, settings: AISettings, storage: S3MediaStorage, record: RecordUsage | None = None
    ) -> None:
        self._record = record
        self._settings = settings
        self._storage = storage

    async def read(self, object_key: str, caption: str | None = None) -> MenuReading | None:
        try:
            data, content_type = await self._storage.get(object_key)
        except MediaStoreError as exc:
            _logger.warning("menu_photo_unreadable", error=str(exc))
            return None
        if len(data) > _MAX_BYTES or not content_type.startswith("image/"):
            return None
        image = f"data:{content_type};base64,{base64.b64encode(data).decode()}"
        for model in dict.fromkeys((self._settings.default_model, self._settings.fallback_model)):
            payload = await self._ask(model, image, caption)
            if payload is not None:
                return _reading(payload)
        return None

    async def lacking_sides(self, names: list[str]) -> list[tuple[str, list[str]]] | None:
        menu = "\n".join(f"- {name}" for name in names)
        for model in dict.fromkeys((self._settings.default_model, self._settings.fallback_model)):
            payload = await self._post(
                model, [{"role": "user", "content": f"{_SIDES_PROMPT}\n\nMenu :\n{menu}"}]
            )
            if payload is None or not isinstance(payload.get("lacking"), list):
                continue
            known = set(names)
            return [
                (str(item["name"]), [str(side) for side in item.get("sides") or []][:4])
                for item in payload["lacking"]
                if isinstance(item, dict) and item.get("name") in known
            ]
        return None

    async def _ask(self, model: str, image: str, caption: str | None) -> dict[str, Any] | None:
        said = f"\nLégende du restaurateur : « {caption.strip()[:200]} »" if caption else ""
        content = [
            {"type": "text", "text": _PROMPT + said},
            {"type": "image_url", "image_url": {"url": image}},
        ]
        return await self._post(model, [{"role": "user", "content": content}])

    async def _post(self, model: str, messages: list[dict[str, Any]]) -> dict[str, Any] | None:
        body = {
            "model": model,
            "temperature": 0,
            "max_tokens": 1500,
            "response_format": {"type": "json_object"},
            "messages": messages,
        }
        headers = {"Authorization": f"Bearer {self._settings.api_key.get_secret_value()}"}
        try:
            async with httpx.AsyncClient(base_url=self._settings.base_url, timeout=40) as client:
                started = time.monotonic()
                response = await client.post("/chat/completions", json=body, headers=headers)
            response.raise_for_status()
            data = response.json()
            await self._usage(model, data, started)
            text = str(data["choices"][0]["message"]["content"]).strip()
            parsed = json.loads(text.strip("`").removeprefix("json").strip())
        except (httpx.HTTPError, KeyError, IndexError, TypeError, ValueError) as exc:
            _logger.warning("menu_reader_failed", model=model, error=type(exc).__name__)
            return None
        return parsed if isinstance(parsed, dict) else None

    async def _usage(self, model: str, data: Any, started: float) -> None:
        if self._record is None:
            return
        try:
            tokens_in, tokens_out = tokens_of(data)
            elapsed = int((time.monotonic() - started) * 1000)
            await self._record("food_menu_reader", model, tokens_in, tokens_out, elapsed)
        except Exception as exc:  # a ledger failure never costs the answer
            _logger.warning("ai_usage_not_recorded", error=type(exc).__name__)


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
    price = payload.get("price")
    described = payload.get("description")
    return MenuReading(
        kind=kind if kind in ("MENU", "DISH", "MIXED") else "OTHER",
        lines=lines,
        dish=dish.strip() if isinstance(dish, str) and dish.strip() else None,
        price=price if isinstance(price, int) and not isinstance(price, bool) else None,
        description=" ".join(described.split())[:200] if isinstance(described, str) else None,
    )
