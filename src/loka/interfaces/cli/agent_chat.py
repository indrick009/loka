"""Talk to the Pydantic AI tenant-search prototype in a terminal.

    uv sync --extra agent
    AI__API_KEY=sk-or-... uv run python -m loka.interfaces.cli.agent_chat \\
        --model <id-de-modèle-openrouter>

Replay a scripted conversation (one user message per line) to compare models:

    uv run python -m loka.interfaces.cli.agent_chat --script tests/fixtures/agent/tenant_fr.txt

Commands while chatting: ``/pin LAT LON`` simulates a shared WhatsApp position,
``/facts`` prints the stored facts, ``/reset`` starts over, ``/quit`` leaves.

Nothing here touches the database or WhatsApp: the catalogue is the demo
Yaoundé, held in memory.
"""

from __future__ import annotations

import argparse
import asyncio
import sys
from pathlib import Path
from typing import Any

from pydantic_ai.messages import ModelMessage

from loka.bounded_contexts.ai.infrastructure.agent.demo_catalogue import DemoCatalogue
from loka.bounded_contexts.ai.infrastructure.agent.models import openrouter_model
from loka.bounded_contexts.ai.infrastructure.agent.tenant_search import TenantSearchAgent
from loka.bounded_contexts.housing.conversation.facts import FactExtractor
from loka.shared.infrastructure.config.settings import get_settings


def _pin(facts: dict[str, Any], args: list[str]) -> str:
    try:
        latitude, longitude = float(args[0]), float(args[1])
    except (IndexError, ValueError):
        return "usage: /pin LAT LON  (ex. /pin 3.8480 11.5021)"
    checked = FactExtractor().validate({"latitude": latitude, "longitude": longitude})
    if "location" not in checked.facts:
        return f"position refusée : {checked.rejected}"
    facts["location"] = checked.facts["location"]
    return f"position enregistrée : {facts['location']}"


async def _run(model_name: str | None, language: str, script: Path | None) -> None:
    settings = get_settings().ai
    agent = TenantSearchAgent(openrouter_model(settings, model_name))
    catalogue = DemoCatalogue.yaounde()
    facts: dict[str, Any] = {}
    history: list[ModelMessage] = []

    lines = script.read_text(encoding="utf-8").splitlines() if script else None
    print(f"Kora (prototype Pydantic AI, {model_name or settings.default_model})")
    while True:
        if lines is not None:
            if not lines:
                break
            text = lines.pop(0).strip()
            if not text or text.startswith("#"):
                continue
            print(f"\n> {text}")
        else:
            try:
                text = input("\n> ").strip()
            except EOFError:
                break
        if not text:
            continue
        if text == "/quit":
            break
        if text == "/facts":
            print(facts)
            continue
        if text == "/reset":
            facts, history = {}, []
            print("(nouvelle conversation)")
            continue
        if text.startswith("/pin"):
            print(_pin(facts, text.split()[1:]))
            continue
        try:
            turn = await agent.reply(
                text, facts=facts, history=history, language=language, search=catalogue
            )
        except Exception as exc:
            print(f"[erreur modèle : {exc}]", file=sys.stderr)
            continue
        facts, history = turn.facts, turn.messages
        print(f"Kora [{turn.act}] : {turn.reply}")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--model", help="OpenRouter model id (default: AI__DEFAULT_MODEL)")
    parser.add_argument("--language", default="fr", choices=("fr", "en"))
    parser.add_argument("--script", type=Path, help="one user message per line")
    args = parser.parse_args()
    asyncio.run(_run(args.model, args.language, args.script))


if __name__ == "__main__":
    main()
