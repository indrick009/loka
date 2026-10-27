"""Deterministic intent classification.

Two entry points, with different jobs:

* :meth:`DeterministicClassifier.shortcut` is the cheap path used when a model
  is available. It only fires on messages that carry no ambiguity at all — an
  exact "oui"/"non" or a bare amount — where spending a model call would be
  pure waste. Everything else goes to the model first.
* :meth:`DeterministicClassifier.classify` is the fuller rule set used when no
  model is configured, so the platform still answers with regexes alone.

Anything neither can resolve returns ``None``, which is the signal to fall back
rather than guess."""

from loka.bounded_contexts.housing.conversation.rules.classifier import (
    DeterministicClassifier,
)
from loka.bounded_contexts.housing.conversation.rules.match import (
    DeterministicMatch,
)
from loka.bounded_contexts.housing.conversation.rules.patterns import (
    ADDS_NEIGHBOURHOODS,
)
from loka.bounded_contexts.housing.conversation.rules.pending import (
    denial_answer,
)
from loka.bounded_contexts.housing.conversation.rules.reading import (
    classify,
    shortcut,
    shorthand_amount,
)
from loka.bounded_contexts.housing.conversation.rules.vocabulary import (
    AFFIRMATIVE,
    NEGATIVE,
)

__all__ = [
    "ADDS_NEIGHBOURHOODS",
    "AFFIRMATIVE",
    "NEGATIVE",
    "DeterministicClassifier",
    "DeterministicMatch",
    "classify",
    "denial_answer",
    "shortcut",
    "shorthand_amount",
]
