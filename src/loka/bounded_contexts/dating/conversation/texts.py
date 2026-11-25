"""Every sentence Dating writes itself."""

from __future__ import annotations

from loka.bounded_contexts.conversation.application.services.complaints import report_hint
from loka.bounded_contexts.dating.domain.profile import Profile


def _fr(language: str) -> bool:
    return not language.startswith("en")


def minor(language: str) -> str:
    if _fr(language):
        return "idia Rencontres est réservé aux personnes majeures. Je ne peux pas créer de profil."
    return "idia Dating is for adults only. I cannot create a profile."


def inappropriate(language: str) -> str:
    if _fr(language):
        return "Ta description ne peut pas être acceptée telle quelle. Reformule-la simplement."
    return "Your description cannot be accepted as it is. Please rephrase it simply."


def ready(language: str) -> str:
    if _fr(language):
        return "Ton profil est prêt !"
    return "Your profile is ready!"


def proposal(other: Profile, language: str) -> str:
    # Never the description: members write it for idia's matching alone, on the
    # promise that nobody else reads it.
    where = f"{other.city} ({other.country})"
    if _fr(language):
        return (
            f"Voici quelqu'un qui pourrait te plaire : {other.first_name}, {other.age} ans, "
            f"{other.profession}, {where}. Vos descriptions vont bien ensemble.\n\n"
            "Tu aimerais faire sa connaissance ? Réponds OUI ou NON. Si vous dites oui "
            "tous les deux, vous pourrez vous poser des questions à travers moi avant "
            "de confirmer."
        )
    return (
        f"Here is someone you might like: {other.first_name}, {other.age}, "
        f"{other.profession}, {where}. Your descriptions fit well together.\n\n"
        "Would you like to meet? Reply YES or NO. If you both say yes, you can ask "
        "each other questions through me before confirming."
    )


def yes_or_no(language: str) -> str:
    if _fr(language):
        return "Réponds simplement OUI si cette personne t'intéresse, ou NON."
    return "Just reply YES if you are interested, or NO."


def _names(names: list[str], fr: bool) -> str:
    if len(names) == 1:
        return names[0]
    return f"{', '.join(names[:-1])} {'et' if fr else 'and'} {names[-1]}"


def standing(waiting: list[str], silent: list[str], language: str) -> str:
    """Where the member's yeses stand: never « refused », « did not follow up »."""
    fr = _fr(language)
    lines = []
    # « Tu as dit oui à Sandra, Joyce : j'attends sa réponse » and « Ornela,
    # Aimée n'a pas donné suite » (journal of 25 Nov 2026, 10:26).
    if waiting:
        names = _names(waiting, fr)
        awaited = "leurs réponses" if len(waiting) > 1 else "sa réponse"
        lines.append(
            f"Tu as dit oui à {names} : j'attends {awaited}, "
            "je te préviens si c'est réciproque."
            if fr
            else f"You said yes to {names}: I am waiting for an answer and will tell you."
        )
    if silent:
        names, verb = _names(silent, fr), "n'ont" if len(silent) > 1 else "n'a"
        lines.append(
            f"{names} {verb} pas donné suite."
            if fr
            else f"{names} did not follow up."
        )
    return "\n".join(lines)


# After the first, quieter answers: the same sentence a third time puts the
# conversation on hold (``services/stuck``), as it did twice on 24 Nov 2026.
_QUIET_FR = (
    "Pas encore de nouveau profil compatible. Je te fais signe dès qu'il y en a un 💛",
    "Rien de neuf pour l'instant de mon côté. En attendant, je peux aussi t'aider pour "
    "un logement, un repas ou un billet de car 🙂",
    "D'accord 👍",
)
_QUIET_EN = (
    "No new compatible profile yet. I will tell you as soon as there is one 💛",
    "Nothing new on my side for now. Meanwhile I can help with a room, a meal or a bus ticket 🙂",
    "All right 👍",
)


def nobody_now(
    state: str, quiet: int, language: str, *, seen_any: bool = True, narrow: bool = False
) -> str:
    """Nobody left to propose: the state first, then something different each time.

    ``seen_any``: whether the member was ever proposed someone; a new profile
    was told « tu as vu tous les profils » (journal of 24 Nov 2026, 16:48).
    ``narrow``: people exist, only where the member looks rules them out.
    """
    if quiet % 4 == 0:
        fr = _fr(language)
        if narrow:
            last = (
                "Personne ne correspond encore à l'endroit où tu cherches, mais d'autres "
                "profils existent ailleurs. Si tu veux les voir, dis-moi « peu importe » 💛"
                if fr
                else "Nobody matches where you look yet, but others exist elsewhere. "
                "Say « anywhere » to see them 💛"
            )
        elif seen_any:
            last = (
                "Tu as vu tous les profils compatibles pour l'instant ; je te présente la "
                "prochaine personne dès qu'elle s'inscrit 💛"
                if fr
                else "You have seen every compatible profile for now; I will introduce the "
                "next one as soon as they join 💛"
            )
        else:
            last = (
                "Je n'ai pas encore de profil compatible avec le tien ; je te présente la "
                "première personne qui te correspond dès qu'elle s'inscrit 💛"
                if fr
                else "No compatible profile yet; I will introduce the first one who fits "
                "as soon as they join 💛"
            )
        return f"{state}\n{last}".strip()
    variants = _QUIET_FR if _fr(language) else _QUIET_EN
    return variants[(quiet % 4) - 1]


_PHOTO_REFUSED_FR = {
    "AI": "Cette photo semble générée ou retouchée par une IA",
    "STOCK": "Cette photo ressemble à une photo de pub ou d'internet",
    "CELEBRITY": "Cette photo montre une personne connue",
    "NO_FACE": "Je ne vois pas bien ton visage sur cette photo",
    "GROUP": "Il y a plusieurs personnes sur cette photo",
    "CHILD": "Cette photo montre un enfant",
    "NOT_PHOTO": "Ce n'est pas une photo de toi",
}


def photo_refused(reason: str | None, language: str) -> str:
    """The photo rule, applied: a real photo of the member, or none."""
    if not _fr(language):
        return (
            "I can't use this photo: idia Dating needs a real photo of you, face visible, "
            "with no AI or filter. Please send another one 📸"
        )
    why = _PHOTO_REFUSED_FR.get(reason or "", "Je ne peux pas utiliser cette photo")
    return (
        f"{why} : je ne peux pas l'utiliser 🙏🏾 idia Rencontres demande une vraie photo de "
        "toi, visage bien visible, sans filtre ni IA. Envoie-m'en une autre 📸"
    )


def no_follow_up(other: Profile) -> str:
    """To a member whose yes got a no: the fact, never the word « refused »."""
    return (
        f"{other.first_name} n'a pas donné suite 💛 Ce n'était peut-être pas la bonne personne. "
        "Tu veux voir d'autres profils ? Écris « rencontre » et je t'en présente."
    )


def photo_updated(language: str) -> str:
    return (
        "📸 Ta photo de profil est mise à jour."
        if _fr(language)
        else "📸 Your profile photo is updated."
    )


def liked(language: str) -> str:
    if _fr(language):
        return (
            "C'est noté ! Je lui présente ton profil ; si c'est réciproque, je te préviens. "
            "Si tu veux découvrir quelqu'un d'autre en attendant, dis-le-moi.\n"
            f"{report_hint(language)}"
        )
    return (
        "Noted! I am introducing your profile; if it is mutual, I will let you know. "
        "If you'd like to meet someone else meanwhile, just tell me.\n"
        f"{report_hint(language)}"
    )


def passed(language: str) -> str:
    return "D'accord." if _fr(language) else "All right."


def match(other: Profile, language: str) -> str:
    if _fr(language):
        return (
            f"C'est un match ! {other.first_name} ({other.age} ans, {other.city}) a dit oui "
            "aussi. idia ne donne jamais un contact sans l'accord des deux : c'est le "
            "cas. Pour obtenir son numéro WhatsApp, réponds PAYER : 1 000 FCFA par "
            "Mobile Money sur ce numéro, ou envoie le numéro à utiliser."
        )
    return (
        f"It's a match! {other.first_name} ({other.age}, {other.city}) said yes too. "
        "Reply PAY to get their WhatsApp number: 1,000 FCFA by Mobile Money."
    )


def unlock_sent(phone: str) -> str:
    return (
        f"J'ai envoyé la demande de paiement de 1 000 FCFA au {phone}. Valide-la sur ton "
        "téléphone : je t'envoie son numéro dès que c'est fait."
    )


def unlock_failed() -> str:
    return "Le paiement n'a pas pu être lancé. Envoie un autre numéro Mobile Money."


def unlock_later() -> str:
    return "D'accord. Réponds PAYER quand tu veux obtenir son numéro."


def pay_or_not() -> str:
    return "Réponds PAYER pour obtenir son numéro (1 000 FCFA), ou NON."


def in_relation(language: str) -> str:
    if _fr(language):
        return (
            "Tu es en relation avec ton dernier match, alors je ne te propose plus "
            "personne pour l'instant 💛 Si ça n'a pas marché entre vous, réponds OUI "
            "et je te remets dans les propositions."
        )
    return (
        "You are seeing your last match, so I am not proposing anyone for now 💛 "
        "If it did not work out, reply YES and I will propose profiles again."
    )


def back_on_market(language: str) -> str:
    if _fr(language):
        return (
            "Dommage, mais ce n'était peut-être pas la bonne personne. "
            "Je relance les propositions."
        )
    return "Too bad, maybe it was not the right person. Back to the proposals."


def stay_in_relation(language: str) -> str:
    if _fr(language):
        return "Super, je vous souhaite le meilleur ! Écris-moi si ça change."
    return "Great, all the best to you both! Write to me if that changes."
