"""Money value object.

Amounts are stored as integers in the smallest currency unit. XAF has no
subunit in practice, so floats never appear anywhere in the pricing path.
"""

from __future__ import annotations

from dataclasses import dataclass

from loka.shared.domain.value_object import ValueObject

DEFAULT_CURRENCY = "XAF"


@dataclass(frozen=True, slots=True)
class Money(ValueObject):

    amount: int
    currency: str = DEFAULT_CURRENCY

    def __post_init__(self) -> None:
        if not isinstance(self.amount, int) or isinstance(self.amount, bool):
            self._reject("amount must be an integer", amount=self.amount)
        if len(self.currency) != 3:
            self._reject("currency must be an ISO-4217 alpha-3 code", currency=self.currency)

    @classmethod
    def zero(cls, currency: str = DEFAULT_CURRENCY) -> Money:
        return cls(amount=0, currency=currency)

    def _same_currency(self, other: Money) -> None:
        if self.currency != other.currency:
            self._reject(
                "currency mismatch",
                left=self.currency,
                right=other.currency,
            )

    def __add__(self, other: Money) -> Money:
        self._same_currency(other)
        return Money(amount=self.amount + other.amount, currency=self.currency)

    def __sub__(self, other: Money) -> Money:
        self._same_currency(other)
        return Money(amount=self.amount - other.amount, currency=self.currency)

    def __mul__(self, factor: int) -> Money:
        return Money(amount=self.amount * factor, currency=self.currency)

    def __lt__(self, other: Money) -> bool:
        self._same_currency(other)
        return self.amount < other.amount

    def __le__(self, other: Money) -> bool:
        self._same_currency(other)
        return self.amount <= other.amount

    def __gt__(self, other: Money) -> bool:
        self._same_currency(other)
        return self.amount > other.amount

    def __ge__(self, other: Money) -> bool:
        self._same_currency(other)
        return self.amount >= other.amount

    @property
    def is_positive(self) -> bool:
        return self.amount > 0

    @property
    def is_zero(self) -> bool:
        return self.amount == 0

    def require_positive(self, *, reason: str) -> Money:
        if not self.is_positive:
            self._reject(reason, amount=self.amount)
        return self

    def require_non_negative(self, *, reason: str) -> Money:
        if self.amount < 0:
            self._reject(reason, amount=self.amount)
        return self

    def require_at_most(self, ceiling: Money, *, reason: str) -> Money:
        if self > ceiling:
            self._reject(reason, amount=self.amount, ceiling=ceiling.amount)
        return self

    def percentage_of(self, ratio: float) -> Money:
        if not 0 < ratio <= 1:
            self._reject("ratio must be within (0, 1]", ratio=ratio)
        return Money(amount=int(self.amount * ratio), currency=self.currency)

    def format(self) -> str:
        return f"{self.amount:,} {self.currency}"