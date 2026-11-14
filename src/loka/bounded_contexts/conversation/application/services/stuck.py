"""Whether a reply about to be sent shows the conversation going round in circles.

Read on the final sentence, whatever lane wrote it — a model, a rule or a
template — because the user does not care which one repeated itself. A reply
found stuck is held back, the user is told an answer is coming, and a person
is alerted (see ``interfaces/worker/stuck_guard``).

Two signals, both narrow on purpose: a conversation paused for nothing is a
cost too.
"""

from __future__ import annotations

from difflib import SequenceMatcher

from loka.bounded_contexts.conversation.application.ports import Exchange

# The same threshold as ``conversation_voice.parrots``: one judgement of "the
# same sentence" for the whole platform.
SAME_RATIO = 0.85
# « C'est noté 👍 » twice is a courtesy, not a loop.
MIN_REPLY_CHARS = 20
# This many turns in a row answered "I did not understand": the first is a
# typo, the second bad luck, the third a user nobody is reading.
MISREAD_REPEATS = 3

# How far back a repeated sentence still counts as going in circles.
RECENT_LINES = 2
# What ``ConversationExchanges`` keeps of a line.
THREAD_CHARS = 280

REPEATED_REPLY = "repeated_reply"
NOT_UNDERSTOOD = "not_understood"


def _same(left: str, right: str) -> bool:
    # The thread keeps the head of each line only: compare like with like.
    a, b = left.casefold().strip()[:THREAD_CHARS], right.casefold().strip()[:THREAD_CHARS]
    return bool(a and b) and SequenceMatcher(None, a, b).ratio() >= SAME_RATIO


def repeats_itself(reply: str, thread: list[Exchange]) -> bool:
    """Whether ``reply`` was already sent in answer to a *different* message.

    ``thread`` ends with the message being answered. The same question asked
    twice may honestly get the same answer; a different message getting the
    sentence Kora just said means the last one was not understood either.
    """
    if len(reply.strip()) < MIN_REPLY_CHARS:
        return False
    asked = next((line.text for line in reversed(thread) if line.speaker == "user"), None)
    if asked is None:
        return False
    # Each of Kora's lines with the message it answered.
    said: list[tuple[str, str]] = []
    before: str | None = None
    for line in thread:
        if line.speaker == "user":
            before = line.text
        elif before is not None:
            said.append((line.text, before))
    # Her last lines only: a menu shown again an hour later is not a loop.
    return any(
        _same(reply, text) and not _same(asked, answered) for text, answered in said[-RECENT_LINES:]
    )


def stuck_signal(reply: str, thread: list[Exchange], *, misread_run: int) -> str | None:
    """The reason this reply must not be sent, or ``None`` when it may."""
    if repeats_itself(reply, thread):
        return REPEATED_REPLY
    if misread_run >= MISREAD_REPEATS:
        return NOT_UNDERSTOOD
    return None
