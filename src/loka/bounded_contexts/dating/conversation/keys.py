"""Where a Dating thread stands: the open question and what it is about."""

from __future__ import annotations

from loka.bounded_contexts.dating.conversation.spec import DATING

LIKE_KEY = DATING.question_key("like")
UNLOCK_KEY = DATING.question_key("unlock")
# Two members asking each other questions through Kora; buying more of them.
TALK_KEY = DATING.question_key("talk")
PLUS_KEY = DATING.question_key("plus")

PROPOSED_KEY = "_proposed"
MATCH_KEY = "_match"
TALK_ID = "_talk"
