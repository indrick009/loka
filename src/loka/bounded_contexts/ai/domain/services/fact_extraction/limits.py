"""Bounds a fact must fall within to be believed."""

from __future__ import annotations

# A rent below this is a misparse, not a negotiation: "150" for "150 000" is the
# most common extraction error, and accepting it would create an unbelievable
# listing that then pollutes search results.
MIN_PLAUSIBLE_RENT_XAF = 10_000


MAX_PLAUSIBLE_RENT_XAF = 500_000_000


# Confidence below this never moves the conversation. The turn is still
# recorded, so "the model was unsure" is measurable instead of being silently
# converted into a wrong listing.
MIN_ACCEPTED_CONFIDENCE = 0.75


# How many listings a result list may offer, and therefore the largest number a
# tenant's choice can name. Lives here rather than with the copy because it is
# a validation bound: a choice outside it is not a choice, whatever the reply
# layer happens to print.
MAX_RESULT_CHOICES = 5
