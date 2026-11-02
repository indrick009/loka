"""What a Food turn does: register, publish, close, search, pass a request on."""

from __future__ import annotations

import uuid
from collections.abc import Awaitable, Callable
from dataclasses import replace
from typing import Any

from loka.bounded_contexts.conversation.application.plugs.forms import (
    chosen,
    say,
    stored,
    xaf,
)
from loka.bounded_contexts.conversation.application.plugs.turn import PlugDesk, PlugTurn
from loka.bounded_contexts.conversation.application.use_cases.analyse_conversation_message.turn import (  # noqa: E501
    TurnOutcome,
)
from loka.bounded_contexts.conversation.domain.entities.conversation_session import (
    FlowName,
    FlowStep,
)
from loka.bounded_contexts.food.conversation import texts
from loka.bounded_contexts.food.conversation.photos import DishPhotos
from loka.bounded_contexts.food.conversation.spec import FOOD
from loka.bounded_contexts.food.domain.menu import DailyMenu, MenuDish, menu_day
from loka.bounded_contexts.food.domain.reader import MenuReader
from loka.bounded_contexts.food.domain.restaurant import DishOffer, Restaurant
from loka.bounded_contexts.food.domain.store import FoodStore
from loka.shared.domain.knowledge.dishes import related_keys
from loka.shared.domain.knowledge.where import km_between

HITS_KEY = "_food_offers"
NEARBY_KM = 15.0
PER_RESTAURANT = 2
MAX_OFFERS = 5

NameOf = Callable[[uuid.UUID], Awaitable[str | None]]


class FoodTurns:
    def __init__(
        self, store: FoodStore, *, name_of: NameOf, reader: MenuReader | None = None
    ) -> None:
        self._store = store
        self._name_of = name_of
        self._photos = DishPhotos(store, reader)

    async def apply(self, turn: PlugTurn, desk: PlugDesk) -> TurnOutcome:
        if turn.session.user_id is None:
            return say(texts.no_open_menu(turn.language))
        if turn.flow is FlowName.FOOD_RESTAURANT:
            return await self._restaurateur(turn, desk, turn.session.user_id)
        return await self._client(turn, desk, turn.session.user_id)

    async def _ask(
        self, turn: PlugTurn, desk: PlugDesk, name: str, prefix: str = ""
    ) -> TurnOutcome:
        field = next(item for item in FOOD.form(turn.flow) if item.name == name)
        key = FOOD.question_key(name)
        await desk.settle(
            turn, step=FlowStep.COLLECT_FIELD, question_key=key, context=stored(turn.known)
        )
        text = f"{prefix}\n{field.ask(turn.language)}".strip()
        return say(text, question=key, step=FlowStep.COLLECT_FIELD, voiced=False)

    async def _done(
        self, turn: PlugTurn, desk: PlugDesk, text: str, voiced: bool = True
    ) -> TurnOutcome:
        await desk.settle(turn, step=FlowStep.PUBLISHED, question_key=None, context={})
        return say(text, step=FlowStep.PUBLISHED, voiced=voiced)

    async def _restaurateur(
        self, turn: PlugTurn, desk: PlugDesk, owner: uuid.UUID
    ) -> TurnOutcome:
        known, lang = turn.known, turn.language
        restaurant = await self._store.restaurant_of(owner)
        if restaurant is not None and turn.intent in ("CLOSE_MENU", "CLOSE_DISHES"):
            return await self._close(turn, desk, restaurant)
        media_key = turn.facts.get("media_key")
        if restaurant is not None and isinstance(media_key, str):
            return await self._photo(turn, desk, restaurant, media_key)
        prefix = ""
        if restaurant is None:
            for name in ("restaurant_name", "place"):
                if not known.get(name):
                    return await self._ask(turn, desk, name)
            restaurant = Restaurant.register(
                owner_user_id=owner,
                name=str(known["restaurant_name"]),
                place=dict(known["place"]),
                now=turn.now,
            )
            await self._store.add_restaurant(restaurant)
            prefix = texts.registered(restaurant.name, restaurant.place, lang)
        dishes = [MenuDish.from_dict(dish) for dish in known.get("dishes") or []]
        last = await self._store.last_menu(restaurant.restaurant_id)
        if not dishes and known.get("same_menu") and last is not None:
            dishes = [replace(dish, available=True) for dish in last.dishes]
        if not dishes:
            hint = texts.same_menu_hint(lang) if last is not None else ""
            return await self._ask(turn, desk, "dishes", f"{prefix} {hint}".strip())
        menu = await self._publish(restaurant, dishes, turn)
        text = texts.menu_published(restaurant.name, menu.open_dishes(), lang)
        return await self._done(turn, desk, f"{prefix}\n{text}".strip(), voiced=False)

    async def _publish(
        self, restaurant: Restaurant, dishes: list[MenuDish], turn: PlugTurn
    ) -> DailyMenu:
        """Today's menu with these dishes added or repriced.

        Added, not replaced: a dish sent later in the day ("eru 1 500") joins the
        menu instead of wiping it, and a plate keeps the photo it already had.
        """
        rid = restaurant.restaurant_id
        today = await self._store.menu_of(rid, menu_day(turn.now))
        before = today or await self._store.last_menu(rid)
        previous = before.dishes if before is not None else []
        kept = today.dishes if today is not None and today.is_open else []
        merged = [old for old in kept if not any(old.is_named(new.name) for new in dishes)]
        for new in dishes:
            had = next((old for old in previous if old.is_named(new.name)), None)
            merged.append(replace(new, media=new.media or (had.media if had else ())))
        if today is not None and today.is_open:
            today.dishes = merged
            menu = today
        else:
            menu = DailyMenu.publish(rid, merged, now=turn.now)
        await self._store.save_menu(menu)
        return menu

    async def _photo(
        self, turn: PlugTurn, desk: PlugDesk, restaurant: Restaurant, media_key: str
    ) -> TurnOutcome:
        lang = turn.language
        caption = turn.text if turn.text and not turn.text.startswith("(") else None
        outcome = await self._photos.take(
            restaurant,
            media_key,
            kind=str(turn.facts.get("media_kind") or ""),
            caption=caption,
            now=turn.now,
            language=lang,
        )
        if outcome.publish:
            menu = await self._publish(restaurant, outcome.publish, turn)
            text = texts.menu_published(restaurant.name, menu.open_dishes(), lang)
            if outcome.unpriced:
                text += "\n\n" + texts.unpriced_left_out(outcome.unpriced, lang)
            return await self._done(turn, desk, text, voiced=False)
        if outcome.ask_dishes:
            return await self._ask(turn, desk, "dishes", outcome.text)
        return await self._done(turn, desk, outcome.text, voiced=False)

    async def _close(
        self, turn: PlugTurn, desk: PlugDesk, restaurant: Restaurant
    ) -> TurnOutcome:
        lang = turn.language
        menu = await self._store.menu_of(restaurant.restaurant_id, menu_day(turn.now))
        if menu is None or not menu.is_open:
            return await self._done(turn, desk, texts.no_open_menu(lang))
        if turn.intent == "CLOSE_MENU":
            menu.close(now=turn.now)
            await self._store.save_menu(menu)
            return await self._done(turn, desk, texts.day_closed(lang))
        closed = menu.close_dishes(list(turn.facts.get("closed_dishes") or []), now=turn.now)
        if not closed:
            return await self._done(turn, desk, texts.no_dish_matched(lang))
        await self._store.save_menu(menu)
        return await self._done(
            turn, desk, texts.dishes_closed(closed, len(menu.open_dishes()), lang)
        )

    async def _client(self, turn: PlugTurn, desk: PlugDesk, client: uuid.UUID) -> TurnOutcome:
        lang = turn.language
        offered = turn.session.context.get(HITS_KEY)
        if (
            not turn.fresh
            and turn.session.step is FlowStep.COLLECT_CHOICE
            and isinstance(offered, list)
            and offered
            and not turn.facts.get("dish")
        ):
            pick = chosen(turn)
            if pick is None or not 1 <= pick <= len(offered):
                return say(
                    texts.pick_a_number(len(offered), lang),
                    question=FOOD.question_key("choice"),
                    step=FlowStep.COLLECT_CHOICE,
                )
            return await self._request(turn, desk, client, offered[pick - 1])
        known = turn.known
        if not known.get("place"):
            return await self._ask(turn, desk, "place")
        place, dish = known["place"], known.get("dish")
        found = await self._search(place, known.get("dish_key"), dish, turn, wide=False)
        wide = not found
        if wide:
            found = await self._search(place, known.get("dish_key"), dish, turn, wide=True)
        if not found:
            # Asked again with nothing new: say so differently, never the same
            # sentence twice (replay of 2 Nov 2026).
            again = offered == []
            context = {**stored(known), HITS_KEY: []}
            await desk.settle(turn, step=FlowStep.COLLECT_FIELD, question_key=None, context=context)
            text = (
                texts.still_nothing(dish, place, lang)
                if again
                else texts.nothing_found(dish, place, lang)
            )
            return say(text, voiced=False)
        context = {
            **stored(known),
            HITS_KEY: [
                {
                    "restaurant": offer.restaurant.name,
                    "owner": str(offer.restaurant.owner_user_id),
                    "restaurant_id": str(offer.restaurant.restaurant_id),
                    "dish": offer.dish_name,
                    "price": offer.price_xaf,
                }
                for offer in found
            ],
        }
        key = FOOD.question_key("choice")
        await desk.settle(turn, step=FlowStep.COLLECT_CHOICE, question_key=key, context=context)
        await self._show_photos(found, client, desk, turn)
        return say(
            texts.offers(found, lang, wide=wide),
            question=key,
            step=FlowStep.COLLECT_CHOICE,
            voiced=False,
        )

    async def _show_photos(
        self, found: list[DishOffer], client: uuid.UUID, desk: PlugDesk, turn: PlugTurn
    ) -> None:
        """One picture per offered dish that has one, numbered like the list."""
        for index, offer in enumerate(found, start=1):
            if offer.media:
                caption = (
                    f"{index}. {offer.dish_name} — {xaf(offer.price_xaf)} — "
                    f"{offer.restaurant.name}"
                )
                await desk.notify(client, caption, now=turn.now, media_object_key=offer.media[0])

    async def _search(
        self,
        place: dict[str, Any],
        key: str | None,
        dish: str | None,
        turn: PlugTurn,
        *,
        wide: bool,
    ) -> list[DishOffer]:
        """Near the client first; ``wide`` is the whole of his town."""
        keys = related_keys(key) if key else frozenset()
        found: list[DishOffer] = []
        for restaurant, menu in await self._store.open_menus(menu_day(turn.now)):
            distance = km_between(place, restaurant.place)
            same_city = restaurant.place.get("city") == place.get("city")
            if wide and not same_city:
                continue
            if not wide and (
                (distance is not None and distance > NEARBY_KM)
                or (distance is None and not same_city)
            ):
                continue
            for item in menu.open_dishes():
                if dish and not ((item.key in keys) if keys else item.is_named(dish)):
                    continue
                found.append(
                    DishOffer(
                        restaurant, menu.menu_id, item.name, item.price_xaf, distance, item.media
                    )
                )
        found.sort(key=lambda offer: offer.distance_km if offer.distance_km is not None else 99.0)
        if dish:
            return found[:MAX_OFFERS]
        # "What is there to eat?" is answered with several places, not one
        # restaurant's whole menu.
        shown: dict[uuid.UUID, int] = {}
        varied: list[DishOffer] = []
        for offer in found:
            rid = offer.restaurant.restaurant_id
            if shown.get(rid, 0) < PER_RESTAURANT:
                shown[rid] = shown.get(rid, 0) + 1
                varied.append(offer)
        return varied[:MAX_OFFERS]

    async def _request(
        self, turn: PlugTurn, desk: PlugDesk, client: uuid.UUID, offer: dict[str, Any]
    ) -> TurnOutcome:
        lang = turn.language
        first_name = await self._name_of(client) or ("Un client" if lang != "en" else "A client")
        await self._store.record_request(
            restaurant_id=uuid.UUID(offer["restaurant_id"]),
            client_user_id=client,
            dish={"name": offer["dish"], "price": offer["price"]},
            now=turn.now,
        )
        await desk.notify(
            uuid.UUID(offer["owner"]),
            texts.to_restaurant(
                first_name, turn.session.phone.e164, offer["dish"], int(offer["price"]), "fr"
            ),
            now=turn.now,
        )
        # Platform words, never rewritten: the voice once confirmed another dish
        # than the one passed on to the restaurant (replay of 1 Nov 2026).
        sent = texts.request_sent(offer["restaurant"], offer["dish"], lang)
        return await self._done(turn, desk, sent, voiced=False)
