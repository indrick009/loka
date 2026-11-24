"""Whether a message only closes the exchange: « ok », « bien compris », « merci 👍 ».

Members often end a conversation that way, and it calls for no answer
(observed live, 24 Nov 2026): replying to each « ok » lengthens the thread
and, repeated, reads as a loop. « oui » and « non » are never closings: they
answer something.
"""

from __future__ import annotations

from loka.bounded_contexts.conversation.application.services.privacy import fold

_WORDS = frozenset(
    (  # noqa: SIM905 - a word list reads best as text
        "ok okay okk okkk oki okey kk d'accord d accord daccord dac dacc bien compris recu note "
        "merci beaucoup bcp cool super parfait top nickel ca marche roule tres "
        "noted thanks thank you got it alright all right idia"
    ).split()
)
_LONGEST = 5


def is_closing(text: str | None) -> bool:
    if not text or not text.strip():
        return False
    words = fold(text).split()
    if not words:
        # Emoji only (👍, 🙏, 👌): fold keeps no letters.
        return True
    return len(words) <= _LONGEST and all(word in _WORDS for word in words)
