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
    if not text or not text.strip() or "?" in text:
        # « ?? » is someone waiting for an answer, never a closing: folded to
        # nothing, it was taken for an emoji and Milly got no reply (journal
        # of 26 Nov 2026, 14:22).
        return False
    words = fold(text).split()
    if not words:
        # Emoji only (👍, 🙏, 👌): fold keeps no letters.
        return True
    return len(words) <= _LONGEST and all(word in _WORDS for word in words)


# « annule tout ça », « laisse tomber », « oublie » in the middle of a form: the
# member wants out (journal of 24 Nov 2026, 14:01: « noo annulé tout ça va »
# was answered with the next profile question).
_CANCEL = frozenset(
    (  # noqa: SIM905 - a word list reads best as text
        "annule annuler annulez annulle cancel abandonne stop"
    ).split()
)
_LET_GO = (
    "laisse tomber", "laisse ca", "laissons tomber", "on arrete", "j arrete", "oublie ca",
    "oublie tout",
)


def is_cancel(text: str | None) -> bool:
    """A short message asking to drop what is under way."""
    words = fold(text or "").replace("'", " ").split()
    if not words or len(words) > 6:
        return False
    plain = " ".join(words)
    return any(word in _CANCEL for word in words) or any(part in plain for part in _LET_GO)


def cancelled(language: str | None) -> str:
    if (language or "fr").startswith("en"):
        return "All right, I dropped it. Tell me if I can help with anything else 🙂"
    return "D'accord, j'ai tout annulé. Dis-moi si je peux t'aider pour autre chose 🙂"
