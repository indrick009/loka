"""Small helpers every plugged domain uses: shared facts, choices, outcomes."""

from __future__ import annotations

import re
from collections.abc import Mapping
from typing import TYPE_CHECKING, Any

from loka.bounded_contexts.conversation.domain.entities.conversation_session import FlowStep
from loka.shared.domain.knowledge.where import place_from

if TYPE_CHECKING:
    from loka.bounded_contexts.conversation.application.plugs.turn import PlugTurn
    from loka.bounded_contexts.conversation.application.use_cases.analyse_conversation_message.turn import (  # noqa: E501
        TurnOutcome,
    )

MEDIA_KEYS = ("media_key", "media_kind")


def common_facts(entities: Mapping[str, Any]) -> tuple[dict[str, Any], dict[str, str]]:
    """The place, a picked line and a photo: read the same way by every domain."""
    facts: dict[str, Any] = {}
    rejected: dict[str, str] = {}
    if any(entities.get(name) is not None for name in ("city", "neighbourhood", "latitude")):
        place = place_from(entities)
        if place is not None:
            facts["place"] = place
        else:
            rejected["place"] = "unknown place"
    choice = entities.get("choice")
    if isinstance(choice, int) and not isinstance(choice, bool) and 1 <= choice <= 9:
        facts["choice"] = choice
    for name in MEDIA_KEYS:
        if isinstance(entities.get(name), str):
            facts[name] = entities[name]
    return facts, rejected


def text_of(raw: Any, *, longest: int = 600) -> str | None:
    cleaned = " ".join(str(raw or "").split())[:longest]
    return cleaned or None


def chosen(turn: PlugTurn) -> int | None:
    """The line number picked, from the model or from a bare digit."""
    if isinstance(turn.facts.get("choice"), int):
        return int(turn.facts["choice"])
    match = re.fullmatch(r"\s*(?:le\s+|n°\s*|numero\s+|numéro\s+)?(\d)\s*\.?\s*", turn.text or "")
    return int(match.group(1)) if match else None


def stored(known: Mapping[str, Any]) -> dict[str, Any]:
    """What a task keeps in its session: its facts, never a photo in transit."""
    return {name: value for name, value in known.items() if name not in MEDIA_KEYS}


def say(
    text: str,
    *,
    question: str | None = None,
    step: FlowStep | None = None,
    voiced: bool = True,
) -> TurnOutcome:
    """The turn's answer. A voiced text is rewritten in the user's own tone;
    a list or a recap goes out exactly as written."""
    from loka.bounded_contexts.conversation.application.use_cases.analyse_conversation_message.turn import (  # noqa: E501
        TurnOutcome,
    )

    return TurnOutcome(
        question=question, next_step=step, reply_text=text, platform_text=text if voiced else None
    )


def xaf(amount: int) -> str:
    return f"{amount:,}".replace(",", " ") + " FCFA"
