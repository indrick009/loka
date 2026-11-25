"""A photo or a video a restaurateur sends: a menu page, or one of his plates.

Never required. A written menu is read into dishes and prices, so a
restaurateur who would rather not type sends it once. A plate goes on the dish
its caption names, else on the dish the picture shows, else on the first dish
still without one — so photos sent in the menu's order land where expected.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field, replace
from datetime import datetime

from loka.bounded_contexts.food.conversation import texts
from loka.bounded_contexts.food.domain.menu import MAX_MEDIA_PER_DISH, MenuDish, menu_day
from loka.bounded_contexts.food.domain.reader import MenuReader
from loka.bounded_contexts.food.domain.restaurant import Restaurant
from loka.bounded_contexts.food.domain.store import FoodStore
from loka.shared.domain.errors import DomainError

THOUSANDS_BELOW = 100  # "ndolè 2" on a board is 2 000 FCFA
# A caption naming a dish and its price: « Eru and fufu 1500 », « soya : 1 000 f ».
_PRICED_CAPTION = re.compile(
    r"^\s*(?P<name>[^\d]{2,60}?)\s*[:\-\u2013=]?\s*(?P<price>\d[\d\s.]{0,8})\s*(?:f|fcfa|frs?|xaf)?\s*$",
    re.IGNORECASE,
)


@dataclass(frozen=True, slots=True)
class PhotoOutcome:
    text: str = ""
    # Dishes read from a written menu, to publish.
    publish: list[MenuDish] = field(default_factory=list)
    # Lines read without a usable price.
    unpriced: list[str] = field(default_factory=list)
    # No menu today to attach a plate to: ask for the dishes again.
    ask_dishes: bool = False
    # The photo went on a dish: what is still missing is asked next.
    attached: bool = False


class DishPhotos:
    def __init__(self, store: FoodStore, reader: MenuReader | None) -> None:
        self._store = store
        self._reader = reader

    async def take(
        self,
        restaurant: Restaurant,
        media_key: str,
        *,
        kind: str,
        caption: str | None,
        now: datetime,
        language: str,
        expected: str | None = None,
    ) -> PhotoOutcome:
        reading = (
            await self._reader.read(media_key)
            if self._reader is not None and kind == "IMAGE"
            else None
        )
        if reading is not None and reading.kind == "MENU" and reading.lines:
            dishes, unpriced = _dishes(reading.lines)
            # The page goes with every dish read on it: sent to whoever looks
            # for one of them, never asked again plate by plate (Friends
            # Chickens' eight pages got thirty photo questions, 25 Nov 2026).
            dishes = [replace(dish, menu_photo=media_key) for dish in dishes]
            if dishes:
                return PhotoOutcome(publish=dishes, unpriced=unpriced)
            return PhotoOutcome(texts.menu_without_prices(unpriced, language), ask_dishes=True)
        priced = _priced_dish(caption, media_key)
        if priced is not None:
            # The plate and its price in one message: it is a dish of the menu,
            # with its photo (journal of 5 Nov 2026: « Eru and fufu 1500 » was
            # answered « j'ai bien ta photo, mais pas de menu du jour »).
            return PhotoOutcome(publish=[priced])
        menu = await self._store.menu_of(restaurant.restaurant_id, menu_day(now))
        if menu is None:
            return PhotoOutcome(texts.photo_before_menu(language), ask_dishes=True)
        if reading is not None and reading.kind == "MIXED":
            # One plate per photo (decision of 25 Nov 2026).
            waiting = [item.name for item in menu.dishes if item.available and not item.media]
            return PhotoOutcome(texts.mixed_photo(waiting, language))
        # The dish idia asked the photo of comes before the model's guess.
        said = [text for text in (caption, expected, reading.dish if reading else None) if text]
        dish = menu.attach(media_key, said)
        if dish is None:
            # Every dish has a photo already: ask which one this is for, rather
            # than « 3 au plus » to dishes holding one (25 Nov 2026, 16:33).
            room = [item.name for item in menu.dishes if len(item.media) < MAX_MEDIA_PER_DISH]
            if room:
                return PhotoOutcome(texts.photo_which_dish(room, language))
            return PhotoOutcome(texts.photos_full(language))
        await self._store.save_menu(menu)
        return PhotoOutcome(texts.photo_added(dish.name, language), attached=True)


def _priced_dish(caption: str | None, media_key: str) -> MenuDish | None:
    match = _PRICED_CAPTION.match(caption or "")
    if match is None:
        return None
    amount = int(re.sub(r"[\s.]", "", match.group("price")))
    amount = amount * 1000 if 0 < amount < THOUSANDS_BELOW else amount
    try:
        dish = MenuDish.of(match.group("name").strip(" .,;"), amount)
    except DomainError:
        return None
    return replace(dish, media=(media_key,))


def _dishes(lines: list[tuple[str, int | None]]) -> tuple[list[MenuDish], list[str]]:
    dishes: list[MenuDish] = []
    unpriced: list[str] = []
    for name, price in lines:
        if price is None:
            unpriced.append(name)
            continue
        amount = price * 1000 if 0 < price < THOUSANDS_BELOW else price
        try:
            dishes.append(MenuDish.of(name, amount))
        except DomainError:
            unpriced.append(name)
    return dishes, unpriced
