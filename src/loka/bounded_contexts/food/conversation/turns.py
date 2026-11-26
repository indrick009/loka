"""What a Food turn does: register, publish, close, search, pass a request on."""

from __future__ import annotations

import re
import uuid
from collections.abc import Awaitable, Callable
from dataclasses import replace
from typing import Any

from loka.bounded_contexts.conversation.application.plugs.forms import (
    ask_field,
    chosen_all,
    say,
    stored,
    xaf,
)
from loka.bounded_contexts.conversation.application.plugs.turn import PlugDesk, PlugTurn
from loka.bounded_contexts.conversation.application.services.privacy import fold
from loka.bounded_contexts.conversation.application.use_cases.analyse_conversation_message.turn import (  # noqa: E501
    TurnOutcome,
)
from loka.bounded_contexts.conversation.domain.entities.conversation_session import (
    FlowName,
    FlowStep,
)
from loka.bounded_contexts.food.conversation import texts
from loka.bounded_contexts.food.conversation.photos import DishPhotos
from loka.bounded_contexts.food.conversation.spec import FAR_KEY, FOOD, HITS_KEY
from loka.bounded_contexts.food.domain.menu import DailyMenu, MenuDish, menu_day
from loka.bounded_contexts.food.domain.reader import MenuReader
from loka.bounded_contexts.food.domain.restaurant import DishOffer, Restaurant
from loka.bounded_contexts.food.domain.store import FoodStore
from loka.shared.domain.knowledge.dishes import (
    MEAL,
    asked_category,
    category_of,
    is_side,
    needs_side,
    related_keys,
)
from loka.shared.domain.knowledge.nearby import FAR_KM, reach
from loka.shared.domain.knowledge.where import km_between, place_label

PER_RESTAURANT = 2
# The question that asks « tu le veux toujours ? » (picks kept under FAR_KEY).
FAR_QUESTION = "ask.food.far"
MAX_OFFERS = 5
SENT_KEY = "request_sent"
PHOTO_SPACING_SECONDS = 2

# Between two things asked for at once: « poulet ou poisson », « ndolè, eru ».
_NAMED = re.compile(r"\s+(?:ou|et|or|and)\s+(?:un |une |des |du |de la |a |some )?|\s*[,+/]\s*")

NameOf = Callable[[uuid.UUID], Awaitable[str | None]]


class FoodTurns:
    def __init__(
        self, store: FoodStore, *, name_of: NameOf, reader: MenuReader | None = None
    ) -> None:
        self._store = store
        self._name_of = name_of
        self._reader = reader
        self._photos = DishPhotos(store, reader)

    async def apply(self, turn: PlugTurn, desk: PlugDesk) -> TurnOutcome:
        if turn.session.user_id is None:
            return say(texts.no_open_menu(turn.language))
        if turn.flow is FlowName.FOOD_RESTAURANT:
            return await self._restaurateur(turn, desk, turn.session.user_id)
        return await self._client(turn, desk, turn.session.user_id)

    async def _sides_question(self, menu: DailyMenu, language: str) -> str:
        """Which accompaniment a sauce comes with, when the menu does not say.

        The model decides, knowing Cameroonian plates: the dish table asked
        « Fried rice au poulet » its accompaniment (journal of 26 Nov 2026,
        11:31). The table answers only when the model cannot.
        """
        dishes = menu.open_dishes()
        lacking = (
            await self._reader.lacking_sides([dish.name for dish in dishes])
            if self._reader is not None and dishes
            else None
        )
        if lacking is None:
            if any(is_side(dish.name) and needs_side(dish.name) is None for dish in dishes):
                return ""
            lacking = [
                (dish.name, list(plate.sides))
                for dish in dishes
                if (plate := needs_side(dish.name))
            ]
        return "\n\n" + texts.ask_sides(lacking, language) if lacking else ""

    async def _ask(
        self, turn: PlugTurn, desk: PlugDesk, name: str, prefix: str = ""
    ) -> TurnOutcome:
        field = next(item for item in FOOD.form(turn.flow) if item.name == name)
        key = FOOD.question_key(name)
        await desk.settle(
            turn, step=FlowStep.COLLECT_FIELD, question_key=key, context=stored(turn.known)
        )
        return ask_field(turn, FOOD, field, prefix=prefix)

    async def _done(
        self,
        turn: PlugTurn,
        desk: PlugDesk,
        text: str,
        voiced: bool = True,
        context: dict[str, Any] | None = None,
    ) -> TurnOutcome:
        await desk.settle(turn, step=FlowStep.PUBLISHED, question_key=None, context=context)
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
        completing = turn.session.context.get(COMPLETING)
        if (
            restaurant is not None
            and not turn.fresh
            and turn.session.context.get("next_question") == COMPLETE_KEY
            and isinstance(completing, str)
            and not known.get("dishes")
            and turn.text
        ):
            return await self._completion_answer(turn, desk, restaurant, completing)
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
        if not dishes and not prefix:
            today = await self._store.menu_of(restaurant.restaurant_id, menu_day(turn.now))
            if today is not None and today.is_open and today.open_dishes():
                live = texts.menu_live(restaurant.name, today.open_dishes(), lang)
                return await self._done(turn, desk, live, voiced=False)
        if not dishes:
            hint = texts.same_menu_hint(lang) if last is not None else ""
            return await self._ask(turn, desk, "dishes", f"{prefix} {hint}".strip())
        menu = await self._publish(restaurant, dishes, turn)
        text = texts.menu_published(restaurant.name, menu.open_dishes(), lang)
        text += await self._sides_question(menu, lang)
        return await self._complete(turn, desk, menu, f"{prefix}\n{text}".strip())

    async def _publish(
        self, restaurant: Restaurant, dishes: list[MenuDish], turn: PlugTurn
    ) -> DailyMenu:
        """Today's menu with these dishes added or repriced.

        Added, not replaced: a dish sent later in the day ("eru 1 500") joins the
        menu instead of wiping it, and a plate keeps the photo it already had.
        """
        rid = restaurant.restaurant_id
        today = await self._store.menu_of(rid, menu_day(turn.now))
        # Every dish ever published, so a taro photographed once is never
        # asked again (decision of 25 Nov 2026).
        previous = (today.dishes if today is not None else []) + await self._store.past_dishes(rid)
        kept = today.dishes if today is not None and today.is_open else []
        merged = [old for old in kept if not any(old.same_line(new.name) for new in dishes)]
        for new in dishes:
            had = [old for old in previous if old.same_line(new.name)]
            media = new.media or next((old.media for old in had if old.media), ())
            about = new.description or next((o.description for o in had if o.description), "")
            page = new.menu_photo or next((o.menu_photo for o in had if o.menu_photo), "")
            merged.append(replace(new, media=media, description=about, menu_photo=page))
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
        asked = turn.session.context.get(COMPLETING)
        outcome = await self._photos.take(
            restaurant,
            media_key,
            kind=str(turn.facts.get("media_kind") or ""),
            caption=caption,
            now=turn.now,
            language=lang,
            expected=asked if isinstance(asked, str) else None,
        )
        if outcome.publish:
            menu = await self._publish(restaurant, outcome.publish, turn)
            text = texts.menu_published(restaurant.name, menu.open_dishes(), lang)
            if outcome.unpriced:
                text += "\n\n" + texts.unpriced_left_out(outcome.unpriced, lang)
            text += await self._sides_question(menu, lang)
            return await self._complete(turn, desk, menu, text)
        if outcome.ask_dishes:
            return await self._ask(turn, desk, "dishes", outcome.text)
        if outcome.attached:
            today = await self._store.menu_of(restaurant.restaurant_id, menu_day(turn.now))
            if today is not None:
                return await self._complete(turn, desk, today, outcome.text)
        return await self._done(turn, desk, outcome.text, voiced=False)

    async def _complete(
        self, turn: PlugTurn, desk: PlugDesk, menu: DailyMenu, prefix: str
    ) -> TurnOutcome:
        """Ask what the next dish lacks: a photo, then a description.

        A dish is complete with a price, a description and a photo; each gap
        is asked once, by the dish's name, and « passer » moves on.
        """
        lang = turn.language
        raw = turn.session.context.get(SKIPPED)
        skipped = [str(item) for item in raw] if isinstance(raw, list) else []
        gap = next(
            (
                (dish, need)
                for dish in menu.open_dishes()
                for need in dish.missing()
                if f"{dish.name}:{need}" not in skipped
            ),
            None,
        )
        if gap is None:
            done = texts.menu_complete(lang) if not skipped else texts.menu_done(lang)
            return await self._done(turn, desk, f"{prefix}\n\n{done}".strip(), voiced=False)
        dish, needs = gap
        await desk.settle(
            turn,
            step=FlowStep.COLLECT_FIELD,
            question_key=COMPLETE_KEY,
            context={COMPLETING: dish.name, NEEDS: needs, SKIPPED: skipped},
        )
        ask = texts.ask_gap(dish.name, str(needs), lang)
        return say(
            f"{prefix}\n\n{ask}".strip(),
            question=COMPLETE_KEY,
            step=FlowStep.COLLECT_FIELD,
            voiced=False,
        )

    async def _completion_answer(
        self, turn: PlugTurn, desk: PlugDesk, restaurant: Restaurant, name: str
    ) -> TurnOutcome:
        """The description asked, or « passer »: then the next gap."""
        lang = turn.language
        menu = await self._store.menu_of(restaurant.restaurant_id, menu_day(turn.now))
        if menu is None:
            return await self._done(turn, desk, texts.no_open_menu(lang))
        needs = str(turn.session.context.get(NEEDS) or "")
        text = (turn.text or "").strip()
        raw = turn.session.context.get(SKIPPED)
        skipped = [str(item) for item in raw] if isinstance(raw, list) else []
        if _SKIP.search(fold(text)):
            await desk.settle(
                turn,
                step=FlowStep.COLLECT_FIELD,
                question_key=COMPLETE_KEY,
                context={SKIPPED: [*skipped, f"{name}:{needs}"]},
            )
            turn.session.context[SKIPPED] = [*skipped, f"{name}:{needs}"]
            return await self._complete(turn, desk, menu, "")
        if needs == "description" and len(text) >= 3:
            menu.describe(name, text)
            await self._store.save_menu(menu)
            return await self._complete(turn, desk, menu, texts.described(name, lang))
        return await self._complete(turn, desk, menu, "")

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
            and not _elsewhere(turn)
        ):
            pending = turn.session.context.get(FAR_KEY)
            if isinstance(pending, list) and pending:
                return await self._far_answer(turn, desk, client, offered, pending)
            picks = chosen_all(turn)
            if not picks and not re.search(r"\d", turn.text or ""):
                # « Je veux pas ça »: no number at all is not a choice; asking
                # for one again looped until the stuck guard (journal of
                # 26 Nov 2026, 08:46).
                context = {k: v for k, v in turn.session.context.items() if k != HITS_KEY}
                await desk.settle(
                    turn, step=FlowStep.COLLECT_FIELD, question_key=None, context=context
                )
                return say(texts.something_else(lang), voiced=False)
            if not picks or not all(1 <= pick <= len(offered) for pick in picks):
                return say(
                    texts.pick_a_number(len(offered), lang),
                    question=FOOD.question_key("choice"),
                    step=FlowStep.COLLECT_CHOICE,
                )
            chosen_offers = [offered[p - 1] for p in picks]
            further = [o for o in chosen_offers if reach(o.get("distance_km")) != "near"]
            if further:
                # Past a 100 F moto ride: the client says yes knowing where it is.
                await desk.settle(
                    turn,
                    step=FlowStep.COLLECT_CHOICE,
                    question_key=FAR_QUESTION,
                    context={**turn.session.context, FAR_KEY: picks},
                )
                return say(
                    texts.confirm_far(further, lang), question=FAR_QUESTION, voiced=False
                )
            # « 1 et 2 »: every dish picked is passed on (journal of 16 Nov
            # 2026, 09:20: only the Poulet DG was).
            return await self._request(turn, desk, client, chosen_offers)
        known = turn.known
        if not known.get("place"):
            return await self._ask(turn, desk, "place")
        place, dish = known["place"], known.get("dish")
        wanted = _wanted(known)
        # Within a 300 F moto ride only (``knowledge/nearby``): nobody takes a
        # bus to bring one dish, so the rest of the town is never offered.
        found = await self._search(place, wanted, dish, turn, wide=False)
        wide = False
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
                    "distance_km": offer.distance_km,
                    "where": place_label(offer.restaurant.place),
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

    async def _far_answer(
        self,
        turn: PlugTurn,
        desk: PlugDesk,
        client: uuid.UUID,
        offered: list[dict[str, Any]],
        picks: list[int],
    ) -> TurnOutcome:
        """« Tu le veux toujours ? »: yes passes it on, no goes back to the list."""
        lang = turn.language
        answer = _yes_no(turn)
        context = {k: v for k, v in turn.session.context.items() if k != FAR_KEY}
        if answer is True:
            return await self._request(
                turn, desk, client, [offered[p - 1] for p in picks if 1 <= p <= len(offered)]
            )
        if answer is False:
            await desk.settle(
                turn,
                step=FlowStep.COLLECT_CHOICE,
                question_key=FOOD.question_key("choice"),
                context=context,
            )
            return say(
                texts.far_declined(lang), question=FOOD.question_key("choice"), voiced=False
            )
        return say(texts.yes_or_no(lang), question=FAR_QUESTION, voiced=False)

    async def _show_photos(
        self, found: list[DishOffer], client: uuid.UUID, desk: PlugDesk, turn: PlugTurn
    ) -> None:
        """One picture per offered dish that has one, numbered like the list.

        Each is held back behind the list: sent at once, the pictures reached
        the client before the list that numbers them (replay of 3 Nov 2026).
        """
        # A menu page holding several of the dishes offered goes once, its
        # caption naming them all.
        pictures: dict[str, list[str]] = {}
        for index, offer in enumerate(found, start=1):
            if offer.media:
                pictures.setdefault(offer.media[0], []).append(
                    f"{index}. {offer.dish_name} — {xaf(offer.price_xaf)}"
                )
        by_picture = {offer.media[0]: offer for offer in found if offer.media}
        for shown, (picture, lines) in enumerate(pictures.items(), start=1):
            caption = "\n".join(lines) + f"\n{by_picture[picture].restaurant.name}"
            await desk.notify(
                client,
                caption,
                now=turn.now,
                media_object_key=picture,
                delay_seconds=PHOTO_SPACING_SECONDS * shown,
            )

    async def _search(
        self,
        place: dict[str, Any],
        wanted: list[str],
        dish: str | None,
        turn: PlugTurn,
        *,
        wide: bool,
    ) -> list[DishOffer]:
        """Near the client first; ``wide`` is the whole of his town."""
        keys = frozenset().union(*(related_keys(key) for key in wanted))
        # Each thing named is also matched by its words: « jus » has no dish
        # key, and « poisson braisé et un jus » found the fish only (live
        # replay of 8 Nov 2026).
        names = [part for part in _NAMED.split(dish or "") if part.strip()]
        kinds = {kind for kind in (asked_category(name) for name in names) if kind}
        found: list[DishOffer] = []
        for restaurant, menu in await self._store.open_menus(menu_day(turn.now)):
            distance = km_between(place, restaurant.place)
            same_city = restaurant.place.get("city") == place.get("city")
            if wide and not same_city:
                continue
            if not wide and (
                (distance is not None and distance > FAR_KM)
                or (distance is None and not same_city)
            ):
                continue
            for item in menu.open_dishes():
                kind = category_of(item.name)
                if not dish and kind != MEAL:
                    # « J'ai faim »: meals, never a juice or a cream.
                    continue
                if (
                    dish
                    and kind not in kinds
                    and item.key not in keys
                    and not any(item.is_named(n) for n in names)
                ):
                    continue
                found.append(
                    DishOffer(
                        restaurant,
                        menu.menu_id,
                        item.name,
                        item.price_xaf,
                        distance,
                        item.photos,
                        item.description,
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
        # Room left is filled from the same places: with one restaurant open,
        # its Sauce gombo Royale was left out for its couscous and tapioca
        # (journal of 25 Nov 2026, 12:04).
        varied += [offer for offer in found if offer not in varied]
        return varied[:MAX_OFFERS]

    async def _request(
        self, turn: PlugTurn, desk: PlugDesk, client: uuid.UUID, offers: list[dict[str, Any]]
    ) -> TurnOutcome:
        lang = turn.language
        known_name = await self._name_of(client)
        first_name = known_name or ("Un client" if lang != "en" else "A client")
        for offer in offers:
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
        sent = texts.requests_sent([(o["restaurant"], o["dish"]) for o in offers], lang)
        # Kept in the session so « tu as ma commande ? » is answered from it.
        record = [
            {
                "dish": offer["dish"],
                "price_xaf": int(offer["price"]),
                "restaurant": offer["restaurant"],
                "client_first_name": known_name or "unknown: WhatsApp did not give it",
                "status": "passed to the restaurant, which calls the client back",
            }
            for offer in offers
        ]
        kept = record[0] if len(record) == 1 else record
        return await self._done(turn, desk, sent, voiced=False, context={SENT_KEY: kept})


_YES = ("oui", "yes", "ok", "d'accord", "dac", "vas-y", "go", "toujours")
_NO = ("non", "no", "laisse", "pas", "trop loin")


# The open question while a dish is being completed, and what it is about.
COMPLETE_KEY = FOOD.question_key("complete")
COMPLETING, NEEDS, SKIPPED = "_completing", "_needs", "_skipped"
_SKIP = re.compile(r"\b(passer|passe|pas de photo|je n en ai pas|plus tard|suivant)\b")


def _yes_no(turn: PlugTurn) -> bool | None:
    if turn.intent == "AFFIRMATIVE":
        return True
    if turn.intent == "NEGATIVE":
        return False
    folded = (turn.text or "").strip().casefold()
    if folded.startswith(_NO):
        return False
    if folded.startswith(_YES):
        return True
    return None


def _elsewhere(turn: PlugTurn) -> bool:
    """« Et à Douala ? » while a list is shown: another town is a new search,
    not a wrong number (journal of 26 Nov 2026, 08:45)."""

    def city(place: object) -> str:
        return fold(str(place.get("city") or "")) if isinstance(place, dict) else ""

    new = city(turn.facts.get("place"))
    return bool(new) and new != city(turn.session.context.get("place"))


def _wanted(known: dict[str, Any]) -> list[str]:
    """The dishes asked for: every one named, else the single key of old sessions."""
    keys = known.get("dish_keys")
    if isinstance(keys, list) and keys:
        return [str(key) for key in keys]
    key = known.get("dish_key")
    return [str(key)] if key else []
