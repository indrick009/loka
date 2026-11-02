"""What a photo a restaurateur sends turns out to be.

A written menu (a board, a sheet) is read into dishes and prices, so it can be
published without typing. A plate is a dish photo. The reader only reports;
what is published is still checked by ``MenuDish.of``.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Literal, Protocol

PhotoKind = Literal["MENU", "DISH", "OTHER"]


@dataclass(frozen=True, slots=True)
class MenuReading:
    kind: PhotoKind
    # A written menu: each line as read, the price None when none is written.
    lines: list[tuple[str, int | None]] = field(default_factory=list)
    # A plate: the dish it shows, when recognisable ("ndolè").
    dish: str | None = None


class MenuReader(Protocol):
    async def read(self, object_key: str) -> MenuReading | None:
        """``None`` when the photo could not be read (no model, provider down)."""
