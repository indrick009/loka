"""Where a Dating thread stands: the open question and what it is about."""

from __future__ import annotations

from loka.bounded_contexts.dating.conversation.spec import DATING

LIKE_KEY = DATING.question_key("like")
UNLOCK_KEY = DATING.question_key("unlock")
# Two members asking each other questions through idia; buying more of them.
TALK_KEY = DATING.question_key("talk")
PLUS_KEY = DATING.question_key("plus")
# In a relation: whether it worked out, or back to the proposals.
BACK_KEY = DATING.question_key("back")

PROPOSED_KEY = "_proposed"
MATCH_KEY = "_match"
TALK_ID = "_talk"
