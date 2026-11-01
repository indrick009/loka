"""What a Food turn does: register, publish, close, search, pass a request on."""

from __future__ import annotations

import uuid
from collections.abc import Awaitable, Callable
from typing import Any

from loka.bounded_contexts.conversation.application.plugs.forms import (
    chosen,
    say,
    stored,
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
from loka.bounded_contexts.food.conversation.spec import FOOD
from loka.bounded_contexts.food.domain.menu import DailyMenu, MenuDish, menu_day
from loka.bounded_contexts.food.domain.restaurant import DishOffer, Restaurant
from loka.bounded_contexts.food.domain.store import FoodStore
from loka.shared.domain.knowledge.dishes import related_keys
from loka.shared.domain.knowledge.where import km_between

HITS_KEY = "_food_offers"
NEARBY_KM = 15.0
MAX_OFFERS = 5

NameOf = Callable[[uuid.UUID], Awaitable[str | None]]


class FoodTurns:
    def __init__(self, store: FoodStore, *, name_of: NameOf) -> None:
        self._store = store
        self._name_of = name_of

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
        dishes = known.get("dishes")
        last = await self._store.last_menu(restaurant.restaurant_id)
        if not dishes and known.get("same_menu") and last is not None:
            dishes = [
                MenuDish(dish.name, dish.price_xaf, dish.key).as_dict() for dish in last.dishes
            ]
        if not dishes:
            hint = texts.same_menu_hint(lang) if last is not None else ""
            return await self._ask(turn, desk, "dishes", f"{prefix} {hint}".strip())
        menu = DailyMenu.publish(
            restaurant.restaurant_id,
            [MenuDish.from_dict(dish) for dish in dishes],
            now=turn.now,
        )
        await self._store.save_menu(menu)
        text = texts.menu_published(restaurant.name, dishes, lang)
        return await self._done(turn, desk, f"{prefix}\n{text}".strip(), voiced=False)

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
        found = await self._search(known["place"], known.get("dish_key"), known.get("dish"), turn)
        if not found:
            context = {**stored(known), HITS_KEY: []}
            await desk.settle(turn, step=FlowStep.COLLECT_FIELD, question_key=None, context=context)
            return say(texts.nothing_found(known.get("dish"), known["place"], lang))
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
        return say(
            texts.offers(found, lang), question=key, step=FlowStep.COLLECT_CHOICE, voiced=False
        )

    async def _search(
        self, place: dict[str, Any], key: str | None, dish: str | None, turn: PlugTurn
    ) -> list[DishOffer]:
        keys = related_keys(key) if key else frozenset()
        found: list[DishOffer] = []
        for restaurant, menu in await self._store.open_menus(menu_day(turn.now)):
            distance = km_between(place, restaurant.place)
            if distance is not None and distance > NEARBY_KM:
                continue
            if distance is None and restaurant.place.get("city") != place.get("city"):
                continue
            for item in menu.open_dishes():
                if dish and not ((item.key in keys) if keys else item.is_named(dish)):
                    continue
                found.append(
                    DishOffer(restaurant, menu.menu_id, item.name, item.price_xaf, distance)
                )
        found.sort(key=lambda offer: offer.distance_km if offer.distance_km is not None else 99.0)
        return found[:MAX_OFFERS]

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
