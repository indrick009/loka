"""Say the platform's sentence in the user's own voice.

The state machine decides *what* Kora says — a demand recorded, a lodging being
checked, criteria waiting for a match. Those sentences used to go out as stock
copy, in formal French, whatever the user had just written. Observed live:
"c'est combien d'avance ?", "tu me comprends même ?" and "rappelle-moi mon
budget" all received the identical "Votre demande est enregistrée".

Here the model rewrites that copy: it reacts to what the user said, keeps every
fact, and matches his register. It may add no fact, and a rewrite that loses or
invents a number is thrown away — the stock sentence is always the fallback.
"""

from __future__ import annotations

import json
import re

from loka.bounded_contexts.ai.application.ports import Exchange
from loka.bounded_contexts.ai.application.services.prompts import (
    DIGRESSION_RULE,
    PERSONA,
    exchanges_as_json,
)
from loka.bounded_contexts.ai.domain.services.ai_provider import Prompt

VOICE_SYSTEM_PROMPT = (
    f"{PERSONA} You receive the user's last message, the recent messages of the "
    "thread, and 'platform_message': what the platform has decided to tell the user, "
    "written as stock copy. Rewrite it as Kora's next WhatsApp message, in your own "
    "words and in the user's language and register (French, English or Cameroonian "
    "Pidgin; 'tu' when the user says 'tu' or writes casually) — never copy its "
    "formal sentences. Keep its facts: every number exactly as written, every place "
    "and property type, and its offer or question, last. Add no number, price, "
    "delay, listing or promise of your own. Start with what the user just said "
    "when platform_message does not answer it: a question gets an honest answer "
    "from the recent messages — entry conditions such as the advance or the "
    "caution are set by each landlord and come with each lodging — and a "
    f"digression gets one warm sentence. {DIGRESSION_RULE} Plain text, no markdown, "
    "at most four short sentences."
)

# Room for one sentence of reaction on top of the platform's own.
_MAX_GROWTH_CHARS = 320

# A number as people write it here: "150 000", "150.000", "150000".
_NUMBER = re.compile(r"\d(?:[\d\s\u202f.]*\d)?")


def build_voice_prompt(
    *, message: str, platform_text: str, exchanges: list[Exchange]
) -> Prompt:
    return Prompt(
        system=VOICE_SYSTEM_PROMPT,
        user=json.dumps(
            {
                "user_message": message,
                "platform_message": platform_text,
                "recent_messages": exchanges_as_json(exchanges),
            },
            ensure_ascii=False,
        ),
        temperature=0.5,
        max_output_tokens=300,
    )


def keeps_the_facts(
    platform_text: str, rewritten: str, *, already_said: tuple[str, ...] = ()
) -> bool:
    """Whether a rewrite keeps every number and invents none.

    Numbers are what a user acts on — a rent, a line to pick, a count of months
    — so they are the line a rewrite may not cross. A number may be added only
    when it was already said in the thread: "ton budget c'est 150 000 FCFA"
    repeats the user's own "150 mil", it does not invent it. Places and types
    are left to the prompt: checking them would need every domain's vocabulary.
    """
    if not rewritten.strip() or len(rewritten) > len(platform_text) + _MAX_GROWTH_CHARS:
        return False
    required = _numbers(platform_text)
    said = _numbers(rewritten)
    known = required.union(*(_numbers(line) for line in already_said))
    return required <= said <= known


# "150 mil" is 150 000 here (see ``market``): the thread says it both ways.
_THOUSANDS = re.compile(r"(\d+)\s*mil(?:le)?\b", re.IGNORECASE)


def _numbers(text: str) -> set[str]:
    found = {re.sub(r"[\s\u202f.]", "", match) for match in _NUMBER.findall(text)}
    return found | {f"{amount}000" for amount in _THOUSANDS.findall(text)}
