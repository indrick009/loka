"""Every sentence Food writes itself. Names and prices are the catalogue's."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import Any

from loka.bounded_contexts.conversation.application.plugs.forms import xaf
from loka.bounded_contexts.conversation.application.services.complaints import report_hint
from loka.bounded_contexts.food.domain.menu import MenuDish
from loka.bounded_contexts.food.domain.restaurant import DishOffer
from loka.shared.domain.knowledge.nearby import reach
from loka.shared.domain.knowledge.where import place_label


def _fr(language: str) -> bool:
    return not language.startswith("en")


def _distance(km: float | None, language: str) -> str:
    if km is None:
        return ""
    if km < 0.15:
        return " (à deux pas)" if _fr(language) else " (right next to you)"
    if reach(km) != "near":
        # Past a 100 F moto ride: said in the list, asked again before ordering.
        far = "un peu plus loin" if _fr(language) else "a bit further"
        return f" ({km:.1f} km, {far})"
    return f" ({km:.1f} km)"


def confirm_far(picked: Sequence[Mapping[str, Any]], language: str) -> str:
    """Before ordering a dish past a 100 F moto ride: still want it, knowing where?"""
    lines = "\n".join(
        f"• « {o.get('dish')} » — {o.get('restaurant')}, {o.get('where') or ''}"
        + (f" (≈ {float(o['distance_km']):.1f} km)" if o.get("distance_km") is not None else "")
        for o in picked
    )
    if _fr(language):
        return (
            f"Ce que tu as choisi n'est pas tout près de toi :\n{lines}\n"
            "Tu le veux toujours ? Réponds OUI ou NON."
        )
    return f"What you picked is not right next to you:\n{lines}\nStill want it? Reply YES or NO."


def far_declined(language: str) -> str:
    if _fr(language):
        return "D'accord. Choisis un autre plat de la liste, ou dis-moi ce que tu cherches."
    return "All right. Pick another dish from the list, or tell me what you're looking for."


def yes_or_no(language: str) -> str:
    return "Réponds OUI ou NON." if _fr(language) else "Reply YES or NO."


def registered(name: str, place: dict[str, Any], language: str) -> str:
    where = place_label(place)
    if _fr(language):
        return (
            f"Ton restaurant « {name} » est enregistré ({where}). "
            "La publication est gratuite pendant la promotion de lancement 🎉"
        )
    return (
        f"Your restaurant \"{name}\" is registered ({where}). "
        "Publishing is free during the launch promotion 🎉"
    )


def ask_sides(lacking: Sequence[tuple[str, Sequence[str]]], language: str) -> str:
    """The accompaniment of each sauce named without one, with the usual ones."""
    first = lacking[0][0]
    these, them = ("ces plats", "les") if len(lacking) > 1 else ("ce plat", "le")
    if _fr(language):
        lines = "\n".join(f"• {name} : {', '.join(sides)}… ?" for name, sides in lacking)
        return (
            f"🍽️ Avec quoi sers-tu {these} ?\n{lines}\n"
            f"Réécris-{them} avec l'accompagnement et le prix, par exemple « {first.lower()} "
            "plantain 2 000 » : je mets le menu à jour. Les clients savent ainsi ce qu'ils "
            "auront dans l'assiette."
        )
    lines = "\n".join(f"• {name}: {', '.join(sides)}…?" for name, sides in lacking)
    return (
        f"🍽️ What do you serve these with?\n{lines}\n"
        f"Write them again with the side and the price, e.g. \"{first.lower()} plantain "
        "2000\": I will update the menu."
    )


def menu_live(name: str, dishes: Sequence[MenuDish], language: str) -> str:
    """Today's menu is already out: what it holds, and how it brings orders.

    Asked for « plus de visibilité et des commandes », a restaurant was asked
    its dishes again, then told of a poulet and a poisson braisé it never
    cooked (journal of 25 Nov 2026, 10:13).
    """
    lines = "\n".join(f"• {dish.name} — {xaf(dish.price_xaf)}" for dish in dishes)
    if _fr(language):
        return (
            f"Ton menu du jour est en ligne ({name}) :\n{lines}\n\n"
            "Tous les clients qui cherchent à manger près de toi le voient jusqu'à minuit, "
            "et leurs commandes t'arrivent ici. Pour en avoir plus : publie ton menu chaque "
            "jour, tôt le matin, avec une photo de chaque plat. La publication est gratuite "
            "pendant la promotion de lancement 🎉\n"
            "Un plat à changer ? Écris-le avec son prix (« eru 1 500 »), ou « le ndolè est "
            "fini »."
        )
    return (
        f"Your menu of the day is live ({name}):\n{lines}\n\n"
        "Every client looking for food near you sees it until midnight, and their orders "
        "reach you here. For more: publish every day, early, with a photo of each dish. "
        "Publishing is free during the launch promotion 🎉"
    )


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


def photo_added(dish: str, language: str) -> str:
    if _fr(language):
        return f"📸 C'est ajouté à « {dish} »."
    return f"📸 Added to \"{dish}\"."


def ask_gap(dish: str, needs: str, language: str) -> str:
    """What one dish still lacks, asked by its name."""
    if _fr(language):
        if needs == "photo":
            return (
                f"📸 Envoie la photo de « {dish} », seul sur la photo, prise par toi. "
                "Pas de photo ? Écris « passer »."
            )
        return (
            f"✍️ Décris « {dish} » en une phrase : ce qu'il y a dans l'assiette, la "
            "quantité, l'accompagnement… Ou écris « passer »."
        )
    if needs == "photo":
        return f"📸 Send the photo of \"{dish}\", alone in the picture. No photo? Say \"skip\"."
    return f"✍️ Describe \"{dish}\" in one sentence, or say \"skip\"."


def described(dish: str, language: str) -> str:
    return f"✅ C'est noté pour « {dish} »." if _fr(language) else f"✅ Noted for \"{dish}\"."


def menu_done(language: str) -> str:
    if _fr(language):
        return "C'est tout pour ton menu 👌 Tu pourras compléter un plat quand tu veux."
    return "Your menu is set 👌 You can complete a dish any time."


def menu_complete(language: str) -> str:
    if _fr(language):
        return "Tous tes plats sont complets : prix, description et photo 👌"
    return "Every dish is complete: price, description and photo 👌"


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


def mixed_photo(waiting: Sequence[str], language: str) -> str:
    """A photo of several plates together: one plate per photo, asked by name."""
    if _fr(language):
        ask = f" Envoie la photo de « {waiting[0]} » seul." if waiting else ""
        return (
            "Cette photo montre plusieurs plats ensemble 🙏🏾 Pour que chaque client voie le "
            f"bon plat, envoie une photo par plat.{ask}"
        )
    ask = f" Send the photo of \"{waiting[0]}\" alone." if waiting else ""
    return f"This photo shows several dishes together: please send one photo per dish.{ask}"


def photo_which_dish(names: Sequence[str], language: str) -> str:
    listed = ", ".join(f"« {name} »" for name in names[:6])
    if _fr(language):
        return (
            "C'est pour quel plat, cette photo ? Renvoie-la avec le nom du plat en légende "
            f"({listed}), ou avec le nom et le prix si c'est un nouveau plat."
        )
    return f"Which dish is this photo for? Send it again with the dish's name ({listed})."


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


def morning_reminder(name: str, yesterday: Sequence[MenuDish]) -> str:
    """Each morning, to a restaurant whose menu of the day is not out yet."""
    text = f"Bonjour 👋 C'est l'heure de publier le menu du jour de {name} 🍲"
    if yesterday:
        lines = "\n".join(f"• {dish.name} — {xaf(dish.price_xaf)}" for dish in yesterday)
        text += (
            f"\n\nTon dernier menu :\n{lines}\n\n"
            "Même chose aujourd'hui ? Réponds « même chose » et je le republie. Sinon, "
            "envoie tes plats avec leur prix, ou une photo de ton menu."
        )
    else:
        text += "\nEnvoie tes plats avec leur prix, ou une photo de ton menu."
    return text + "\nLes clients autour de toi le voient jusqu'à minuit."


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
        about = f"\n   {offer.description[:90]}" if offer.description else ""
        lines.append(
            f"{index}. {offer.dish_name} — {xaf(offer.price_xaf)} — "
            f"{offer.restaurant.name}, {where}{distance}{photo}{about}"
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
    if _fr(language):
        what = dish or "menu du jour"
        return (
            f"Aucun restaurant n'a encore publié de {what} aujourd'hui près de {where} "
            "(à moins de 300 F de moto). Essaie un autre plat, ou réessaie un peu plus tard."
        )
    what = dish or "menu of the day"
    return (
        f"No restaurant has published a {what} today near {where} (within a 300 F moto "
        "ride). Try another dish, or try again a bit later."
    )


def still_nothing(dish: str | None, place: dict[str, Any], language: str) -> str:
    # Near the place, not the town: « toujours rien à Douala » was said for
    # Bonabéri while a restaurant had published in Douala (25 Nov 2026, 12:04).
    city = place_label(place)
    if _fr(language):
        what = f"de {dish} " if dish else ""
        return (
            f"Toujours rien {what}près de {city} pour le moment : les restaurants publient leur "
            "menu au fil de la journée. Réessaie un peu plus tard, ou dis-moi un plat précis "
            "(ndolè, poulet DG, poisson braisé…)."
        )
    return (
        f"Still nothing near {city} for now: restaurants publish during the day. Try again "
        "a bit later, or name a dish."
    )


def pick_a_number(count: int, language: str) -> str:
    if _fr(language):
        return f"Réponds avec le numéro du plat, entre 1 et {count}."
    return f"Reply with the dish number, between 1 and {count}."


def something_else(language: str) -> str:
    if _fr(language):
        return (
            "D'accord 🙂 Dis-moi le plat qui te ferait envie, ou une autre ville, "
            "et je cherche."
        )
    return "All right 🙂 Tell me a dish you'd like, or another town, and I'll look."


def request_sent(restaurant: str, dish: str, language: str) -> str:
    if _fr(language):
        return (
            f"J'ai transmis ta demande pour « {dish} » à {restaurant}, avec ton prénom et ce "
            "numéro WhatsApp. Le restaurant va te contacter sur ce numéro d'ici peu.\n"
            f"{report_hint(language)}"
        )
    return (
        f"I passed your request for « {dish} » to {restaurant}, with your first name and "
        "this WhatsApp number. The restaurant will contact you on this number shortly.\n"
        f"{report_hint(language)}"
    )


def requests_sent(picked: list[tuple[str, str]], language: str) -> str:
    """One confirmation for every dish passed on, each with its restaurant."""
    if len(picked) == 1:
        restaurant, dish = picked[0]
        return request_sent(restaurant, dish, language)
    lines = "\n".join(f"• « {dish} » — {restaurant}" for restaurant, dish in picked)
    if _fr(language):
        return (
            f"J'ai transmis tes demandes, avec ton prénom et ce numéro WhatsApp :\n{lines}\n"
            "Chaque restaurant va te contacter sur ce numéro d'ici peu.\n"
            f"{report_hint(language)}"
        )
    return (
        f"I passed on your requests, with your first name and this WhatsApp number:\n{lines}\n"
        "Each restaurant will contact you on this number shortly.\n"
        f"{report_hint(language)}"
    )


def to_restaurant(client: str, phone: str, dish: str, price: int, language: str) -> str:
    if _fr(language):
        return (
            f"Nouvelle demande idia : {client} souhaite réserver « {dish} » "
            f"({xaf(price)}). Contactez-le sur WhatsApp ou par appel : {phone}. "
            "Merci de le rappeler rapidement."
        )
    return (
        f"New idia request: {client} would like to book \"{dish}\" ({xaf(price)}). "
        f"Contact them on WhatsApp or by phone: {phone}."
    )
