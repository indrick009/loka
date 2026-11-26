"""Where a Dating thread stands: the open question and what it is about."""

from __future__ import annotations

from loka.bounded_contexts.dating.conversation.spec import DATING

LIKE_KEY = DATING.question_key("like")
UNLOCK_KEY = DATING.question_key("unlock")
# Two members asking each other questions through idia; buying more of them.
TALK_KEY = DATING.question_key("talk")
PLUS_KEY = DATING.question_key("plus")
# A message in a talk that may not be meant for the other member: idia asks
# before relaying it, the text kept until the answer.
RELAY_KEY = DATING.question_key("relay")
PENDING_RELAY = "_relay"
# In a relation: whether it worked out, or back to the proposals.
BACK_KEY = DATING.question_key("back")
# A plain fact about the member another's wish needs (see ``attributes``).
TRAIT_KEY = DATING.question_key("trait")
ASKED_TRAIT = "_trait"
# Whose yes waits on that answer: the person the member said yes to.
FOR_KEY = "_trait_for"

PROPOSED_KEY = "_proposed"
MATCH_KEY = "_match"
TALK_ID = "_talk"
# Why a profile proposed was turned down, asked after the « non »: read into
# the member's wishes (decision of 26 Nov 2026).
REASON_KEY = DATING.question_key("reason")
REASON_FOR = "_reason_for"
# How many times the member let the question go: after two, it is not asked.
REASON_SKIPS = "_reason_skips"
