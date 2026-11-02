"""Every sentence Food writes itself. Names and prices are the catalogue's."""

from __future__ import annotations

from collections.abc import Sequence
from typing import Any

from loka.bounded_contexts.conversation.application.plugs.forms import xaf
from loka.bounded_contexts.food.domain.menu import MenuDish
from loka.bounded_contexts.food.domain.restaurant import DishOffer
from loka.shared.domain.knowledge.where import place_label


def _fr(language: str) -> bool:
    return not language.startswith("en")


def _distance(km: float | None, language: str) -> str:
    if km is None:
        return ""
    if km < 0.15:
        return " (à deux pas)" if _fr(language) else " (right next to you)"
    return f" ({km:.1f} km)"


def registered(name: str, place: dict[str, Any], language: str) -> str:
    where = place_label(place)
    if _fr(language):
        return f"Ton restaurant « {name} » est enregistré ({where})."
    return f"Your restaurant \"{name}\" is registered ({where})."


def menu_published(name: str, dishes: Sequence[MenuDish], language: str) -> str:
    lines = "\n".join(
        f"• {dish.name} — {xaf(dish.price_xaf)}{' 📷' if dish.media else ''}" for dish in dishes
    )
    bare = [dish.name for dish in dishes if not dish.media]
    if _fr(language):
        text = (
            f"C'est publié ! Menu du jour de {name} :\n{lines}\n\n"
            "Les clients qui cherchent à manger près de toi le voient jusqu'à minuit. "
            "Quand un plat est fini, écris par exemple « le ndolè est fini » ; à la "
            "fermeture, « c'est fini pour aujourd'hui »."
        )
        if bare:
            text += (
                "\n\n📸 Si tu veux, envoie une photo ou une courte vidéo de chaque plat, "
                "prise par toi (pas une image d'internet) : les clients choisissent avec "
                "les yeux, et les avis seront plus justes. Mets le nom du plat en légende, "
                "ou envoie-les dans l'ordre du menu. Ce n'est pas obligatoire, et une photo "
                "envoyée une fois reste pour les jours suivants."
            )
        return text
    text = (
        f"Published! Today's menu at {name}:\n{lines}\n\n"
        "Clients looking for food near you see it until midnight. When a dish runs "
        "out, say so (\"the ndole is finished\"); at closing, \"we are done for today\"."
    )
    if bare:
        text += (
            "\n\n📸 If you like, send a photo or a short video of each dish, taken by you "
            "(not from the internet). Put the dish name as caption, or send them in the "
            "menu's order. It is optional, and a photo sent once stays for the next days."
        )
    return text


def photo_added(dish: str, waiting: Sequence[str], language: str) -> str:
    if _fr(language):
        rest = (
            f"Encore sans photo : {', '.join(waiting)}. Envoie la suivante quand tu veux."
            if waiting
            else "Tous tes plats ont leur photo, bravo 👌"
        )
        return f"📸 C'est ajouté à « {dish} ». {rest}"
    rest = (
        f"Still without a photo: {', '.join(waiting)}."
        if waiting
        else "Every dish has its photo now 👌"
    )
    return f"📸 Added to \"{dish}\". {rest}"


def photo_before_menu(language: str) -> str:
    if _fr(language):
        return (
            "J'ai bien ta photo, mais tu n'as pas encore de menu du jour : je ne sais pas "
            "à quel plat la mettre. Donne-moi d'abord tes plats, puis renvoie les photos."
        )
    return (
        "I got your photo, but you have no menu today yet, so I cannot tell which dish "
        "it belongs to. Give me your dishes first, then send the photos again."
    )


def photos_full(language: str) -> str:
    if _fr(language):
        return "Chaque plat de ton menu a déjà ses photos (3 au plus par plat)."
    return "Every dish on your menu already has its photos (3 at most per dish)."


def menu_without_prices(names: Sequence[str], language: str) -> str:
    listed = ", ".join(names[:8])
    if _fr(language):
        return (
            f"J'ai lu ton menu ({listed}), mais je n'y vois pas les prix. "
            "Écris-moi les plats avec leur prix."
        )
    return f"I read your menu ({listed}) but could not see the prices."


def unpriced_left_out(names: Sequence[str], language: str) -> str:
    listed = ", ".join(names[:8])
    if _fr(language):
        return (
            f"Je n'ai pas lu de prix pour : {listed}. Pour les ajouter, écris-les avec "
            "leur prix, par exemple « eru 1 500 »."
        )
    return f"No price read for: {listed}. Send them with a price to add them."


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


def offers(found: Sequence[DishOffer], language: str, *, wide: bool = False) -> str:
    lines = []
    for index, offer in enumerate(found, start=1):
        distance = _distance(offer.distance_km, language)
        where = place_label(offer.restaurant.place)
        photo = " 📷" if offer.media else ""
        lines.append(
            f"{index}. {offer.dish_name} — {xaf(offer.price_xaf)} — "
            f"{offer.restaurant.name}, {where}{distance}{photo}"
        )
    body = "\n".join(lines)
    if _fr(language):
        head = (
            "Rien de publié tout près de toi, mais voici ce qui se mange aujourd'hui en ville :"
            if wide
            else "Voici ce qui se mange aujourd'hui près de toi :"
        )
        return (
            f"{head}\n{body}\n\n"
            "Réponds avec le numéro du plat qui te tente : je transmets ta demande au restaurant."
        )
    head = (
        "Nothing published right next to you, but here is what is cooking in town today:"
        if wide
        else "Here is what is cooking today near you:"
    )
    return (
        f"{head}\n{body}\n\n"
        "Reply with the number of the dish you want: I will pass your request on."
    )


def nothing_found(dish: str | None, place: dict[str, Any], language: str) -> str:
    where = place_label(place)
    city = place.get("city") or where
    if _fr(language):
        what = dish or "menu du jour"
        return (
            f"Aucun restaurant n'a encore publié de {what} aujourd'hui près de {where}, "
            f"ni ailleurs à {city}. Essaie un autre plat, ou une autre ville."
        )
    what = dish or "menu of the day"
    return (
        f"No restaurant has published a {what} today near {where}, nor elsewhere in "
        f"{city}. Try another dish, or another town."
    )


def still_nothing(dish: str | None, place: dict[str, Any], language: str) -> str:
    city = place.get("city") or place_label(place)
    if _fr(language):
        what = f"de {dish} " if dish else ""
        return (
            f"Toujours rien {what}à {city} pour le moment : les restaurants publient leur "
            "menu au fil de la journée. Réessaie un peu plus tard, ou dis-moi un plat précis "
            "(ndolè, poulet DG, poisson braisé…)."
        )
    return (
        f"Still nothing in {city} for now: restaurants publish during the day. Try again "
        "a bit later, or name a dish."
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
