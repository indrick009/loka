"""Base class for value objects.

Value objects are frozen, compared by structural equality and validate
themselves in ``__post_init__``. They carry no identity across time.
"""

from __future__ import annotations

from typing import Any, ClassVar, NoReturn

from loka.shared.domain.errors import InvariantViolation


class ValueObject:
    __slots__ = ()

    _comparables: ClassVar[tuple[str, ...]] = ()

    def __post_init__(self) -> None:
        self.validate()

    def validate(self) -> None:
        """Reject invalid states. Called once by ``__post_init__``."""

    @classmethod
    def _violation(cls, reason: str, **context: Any) -> InvariantViolation:
        """Build the error without needing an instance (factories, ``normalize``)."""
        return InvariantViolation(f"{cls.__name__}: {reason}", context=context)

    def _reject(self, reason: str, **context: Any) -> NoReturn:
        raise self._violation(reason, **context)

    def __eq__(self, other: object) -> bool:
        if other.__class__ is not self.__class__:
            return NotImplemented
        keys = self._comparables or tuple(
            name for name in getattr(self, "__slots__", ()) if not name.startswith("_")
        )
        return all(getattr(self, key) == getattr(other, key) for key in keys)

    def __hash__(self) -> int:
        keys = self._comparables or tuple(
            name for name in getattr(self, "__slots__", ()) if not name.startswith("_")
        )
        return hash((self.__class__, *(getattr(self, key) for key in keys)))

    def to_primitive(self) -> Any:
        return {key: getattr(self, key) for key in self._comparables}