"""Every sentence Food writes itself. Names and prices are the catalogue's."""

from __future__ import annotations

from collections.abc import Sequence
from typing import Any

from loka.bounded_contexts.conversation.application.plugs.forms import xaf
from loka.bounded_contexts.food.domain.restaurant import DishOffer
from loka.shared.domain.knowledge.where import place_label


def _fr(language: str) -> bool:
    return not language.startswith("en")


def registered(name: str, place: dict[str, Any], language: str) -> str:
    where = place_label(place)
    if _fr(language):
        return f"Ton restaurant « {name} » est enregistré ({where})."
    return f"Your restaurant \"{name}\" is registered ({where})."


def menu_published(name: str, dishes: Sequence[dict[str, Any]], language: str) -> str:
    lines = "\n".join(f"• {dish['name']} — {xaf(int(dish['price']))}" for dish in dishes)
    if _fr(language):
        return (
            f"C'est publié ! Menu du jour de {name} :\n{lines}\n\n"
            "Les clients qui cherchent à manger près de toi le voient jusqu'à minuit. "
            "Quand un plat est fini, écris par exemple « le ndolè est fini » ; à la "
            "fermeture, « c'est fini pour aujourd'hui »."
        )
    return (
        f"Published! Today's menu at {name}:\n{lines}\n\n"
        "Clients looking for food near you see it until midnight. When a dish runs "
        "out, say so (\"the ndole is finished\"); at closing, \"we are done for today\"."
    )


def same_menu_hint(language: str) -> str:
    if _fr(language):
        return "Tu peux aussi écrire « même menu qu'hier »."
    return "You can also say \"same menu as yesterday\"."


def no_open_menu(language: str) -> str:
    if _fr(language):
        return (
            "Tu n'as pas de menu du jour ouvert aujourd'hui. "
            "Envoie-moi tes plats avec leurs prix."
        )
    return "You have no menu open today. Send me your dishes with their prices."


def dishes_closed(closed: Sequence[str], still_open: int, language: str) -> str:
    names = ", ".join(closed)
    if _fr(language):
        rest = (
            f"Il reste {still_open} plat(s) visible(s)."
            if still_open
            else "Ton menu du jour est fermé."
        )
        return f"C'est noté : {names} ne sera plus proposé aux clients. {rest}"
    rest = f"{still_open} dish(es) still visible." if still_open else "Your menu is closed."
    return f"Noted: {names} will no longer be offered. {rest}"


def no_dish_matched(language: str) -> str:
    if _fr(language):
        return "Je ne trouve pas ce plat dans ton menu du jour. Lequel est fini exactement ?"
    return "I cannot find that dish on today's menu. Which one is finished?"


def day_closed(language: str) -> str:
    if _fr(language):
        return "C'est noté, ton menu du jour est fermé. Bonne fin de journée ! À demain."
    return "Noted, today's menu is closed. See you tomorrow."


def offers(found: Sequence[DishOffer], language: str) -> str:
    lines = []
    for index, offer in enumerate(found, start=1):
        distance = f" ({offer.distance_km:.1f} km)" if offer.distance_km is not None else ""
        where = place_label(offer.restaurant.place)
        lines.append(
            f"{index}. {offer.dish_name} — {xaf(offer.price_xaf)} — "
            f"{offer.restaurant.name}, {where}{distance}"
        )
    body = "\n".join(lines)
    if _fr(language):
        return (
            f"Voici ce qui se mange aujourd'hui près de toi :\n{body}\n\n"
            "Réponds avec le numéro du plat qui te tente : je transmets ta demande au restaurant."
        )
    return (
        f"Here is what is cooking today near you:\n{body}\n\n"
        "Reply with the number of the dish you want: I will pass your request on."
    )


def nothing_found(dish: str | None, place: dict[str, Any], language: str) -> str:
    where = place_label(place)
    if _fr(language):
        what = f"de {dish}" if dish else "de menu du jour"
        return (
            f"Je n'ai trouvé aucun {what.removeprefix('de ')} publié aujourd'hui près de {where}. "
            "Essaie un autre plat, ou donne-moi un quartier voisin."
        )
    what = dish or "menu of the day"
    return (
        f"I found no {what} published today near {where}. Try another dish, or a "
        "neighbouring district."
    )


def pick_a_number(count: int, language: str) -> str:
    if _fr(language):
        return f"Réponds avec le numéro du plat, entre 1 et {count}."
    return f"Reply with the dish number, between 1 and {count}."


def request_sent(restaurant: str, dish: str, language: str) -> str:
    if _fr(language):
        return (
            f"J'ai transmis ta demande pour « {dish} » à {restaurant}, avec ton prénom et ce "
            "numéro WhatsApp. Le restaurant va te contacter sur ce numéro d'ici peu. Si ce "
            "n'est pas le cas, reviens me le dire."
        )
    return (
        f"I passed your request for « {dish} » to {restaurant}, with your first name and "
        "this WhatsApp number. The restaurant will contact you on this number shortly. If "
        "it does not, come back and tell me."
    )


def to_restaurant(client: str, phone: str, dish: str, price: int, language: str) -> str:
    if _fr(language):
        return (
            f"Nouvelle demande Kora : {client} souhaite réserver « {dish} » "
            f"({xaf(price)}). Contactez-le sur WhatsApp ou par appel : {phone}. "
            "Merci de le rappeler rapidement."
        )
    return (
        f"New Kora request: {client} would like to book \"{dish}\" ({xaf(price)}). "
        f"Contact them on WhatsApp or by phone: {phone}."
    )
