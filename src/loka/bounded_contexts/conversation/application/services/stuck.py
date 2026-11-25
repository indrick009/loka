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

# A receipt (« ✓ Transmis à Jordan. Il te reste 7 message(s). ») is the same
# sentence after every message relayed, by nature: taken for a loop, it held
# both members mid-talk for two hours (journal of 25 Nov 2026, 21:55).
RECEIPT = "✓"

# How far back a repeated sentence still counts as going in circles.
RECENT_LINES = 4
# The same sentence a second time is often honest — a question asked again
# after a remark (journal of 16 Nov 2026, 09:27: « meublé » before the rent).
# The third time, the thread is going round in circles.
EARLIER_REPEATS = 2
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
    sentence idia just said means the last one was not understood either.
    """
    if len(reply.strip()) < MIN_REPLY_CHARS or reply.lstrip().startswith(RECEIPT):
        return False
    asked = next((line.text for line in reversed(thread) if line.speaker == "user"), None)
    if asked is None:
        return False
    # Each of idia's lines with the message it answered.
    said: list[tuple[str, str]] = []
    before: str | None = None
    for line in thread:
        if line.speaker == "user":
            before = line.text
        elif before is not None:
            said.append((line.text, before))
    # Her last lines only: a menu shown again an hour later is not a loop.
    repeats = sum(
        1
        for text, answered in said[-RECENT_LINES:]
        if _same(reply, text) and not _same(asked, answered)
    )
    return repeats >= EARLIER_REPEATS


def stuck_signal(reply: str, thread: list[Exchange], *, misread_run: int) -> str | None:
    """The reason this reply must not be sent, or ``None`` when it may."""
    if repeats_itself(reply, thread):
        return REPEATED_REPLY
    if misread_run >= MISREAD_REPEATS:
        return NOT_UNDERSTOOD
    return None
