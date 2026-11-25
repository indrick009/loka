"""Who a talk message is for, read by a model on OpenRouter.

Any failure answers ``None``: the general reading of the message decides.
"""

from __future__ import annotations

import json
from typing import Any

import httpx

from loka.bounded_contexts.dating.domain.talk_reader import DECISIONS, TalkLine, TalkReading
from loka.shared.domain.knowledge.how_idia_works import GUIDE
from loka.shared.infrastructure.config.settings import AISettings
from loka.shared.infrastructure.logging import get_logger

_logger = get_logger(__name__)

_PROMPT = (
    "Tu es idia, un assistant WhatsApp au Cameroun. {member} et {other} se sont dit oui "
    "sur idia Rencontre : ils échangent à travers toi, numéros cachés. Tout ce que "
    "{member} t'écrit pendant cette discussion est d'habitude pour {other}, et tu le lui "
    "transmets.\n"
    "Lis le nouveau message de {member} avec les derniers messages, comme le ferait une "
    "personne, et décide :\n"
    "- MATCH : pour {other}. « tu », « toi », « ton » désignent {other}. Une question sur "
    "sa vie, même courte (« quel quartier ? », « pourquoi ? »), une réponse à ce que "
    "{other} a demandé, une demande de photo de lui ou d'elle : MATCH. Mets dans "
    "relay_text les mots exacts pour {other}, copiés du message, sans l'instruction qui "
    "t'est faite autour (« demande-lui « tu habites où ? » » donne « tu habites où ? » ; "
    "un message entièrement pour {other} est copié tel quel).\n"
    "- IDIA : pour toi, sur le fonctionnement de la discussion ou de l'app, changer sa "
    "propre photo de profil, une remarque sur ton dernier message. Réponds dans reply, "
    "en français, en tutoyant, en une ou deux phrases, sans rien inventer sur {other}.\n"
    "- END : {member} ne veut plus continuer avec {other}, ou demande d'autres profils.\n"
    "- CONFIRM : {member} décide d'aller plus loin avec {other} (« CONFIRMER »).\n"
    "- SEND : {member} te demande de transmettre le message gardé (« envoie-lui », "
    "« transmets », « send to him »), ou dit oui après que tu as demandé si tu "
    "l'envoyais.\n"
    "- KEEP : {member} dit de ne pas envoyer le message gardé.\n"
    "- UNCLEAR : seulement si tu ne peux vraiment pas savoir.\n"
    "Message gardé, pas encore transmis : {kept}\n"
    "Comment marche idia Rencontre : {guide}\n"
    'Réponds en JSON : {{"decision": "...", "relay_text": "..." ou null, '
    '"reply": "..." ou null}}.'
)


class OpenRouterTalkReader:
    def __init__(self, settings: AISettings) -> None:
        self._settings = settings

    async def read(
        self,
        *,
        member: str,
        other: str,
        thread: list[TalkLine],
        kept: str | None,
        message: str,
    ) -> TalkReading | None:
        system = _PROMPT.format(
            member=member or "le membre",
            other=other or "l'autre membre",
            kept=f"« {kept} »" if kept else "aucun",
            guide=GUIDE["Rencontre"],
        )
        lines = "\n".join(
            f"{member if line.speaker == 'member' else 'idia'} : {line.text}" for line in thread
        )
        user = (
            f"Derniers messages :\n{lines or '(aucun)'}\n\n"
            f"Nouveau message de {member} :\n{message}"
        )
        for model in dict.fromkeys((self._settings.default_model, self._settings.fallback_model)):
            payload = await self._ask(model, system, user)
            if payload is None or payload.get("decision") not in DECISIONS:
                continue
            reading = TalkReading(
                decision=str(payload["decision"]),
                relay_text=_text(payload.get("relay_text")),
                reply=_text(payload.get("reply")),
            )
            _logger.info("talk_read", decision=reading.decision)
            return reading
        return None

    async def _ask(self, model: str, system: str, user: str) -> dict[str, Any] | None:
        body = {
            "model": model,
            "temperature": 0,
            "max_tokens": 300,
            "response_format": {"type": "json_object"},
            "messages": [
                {"role": "system", "content": system},
                {"role": "user", "content": user},
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
            _logger.warning("talk_reader_failed", model=model, error=type(exc).__name__)
            return None
        return parsed if isinstance(parsed, dict) else None


def _text(raw: object) -> str | None:
    text = " ".join(str(raw).split()) if isinstance(raw, str) else ""
    return text or None
