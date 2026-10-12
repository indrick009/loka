"""The whole content of an avis: one mark out of five.

Deliberately this small. An avis is an **internal ranking signal and nothing
more** (§5), so it needs only enough to order offers. Free text would have to
be moderated, defended against fake reviews, and argued over with a provider
Kora cannot contradict — it was never present for the relationship. Text
belongs on a complaint, because a human reads that one before sanctioning.
"""

from __future__ import annotations

from dataclasses import dataclass

from loka.shared.domain.value_object import ValueObject

MIN_RATING = 1
MAX_RATING = 5


@dataclass(frozen=True, slots=True)
class Rating(ValueObject):
    """A mark from 1 to 5. Never averaged for display, only for ordering."""

    value: int

    def __post_init__(self) -> None:
        if not isinstance(self.value, int) or isinstance(self.value, bool):
            self._reject("rating must be an integer", value=self.value)
        if not MIN_RATING <= self.value <= MAX_RATING:
            self._reject(
                f"rating must be within {MIN_RATING}..{MAX_RATING}", value=self.value
            )

    def to_primitive(self) -> int:
        return self.value
