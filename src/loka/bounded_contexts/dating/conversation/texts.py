"""Every sentence Dating writes itself."""

from __future__ import annotations

from loka.bounded_contexts.dating.domain.profile import Profile


def _fr(language: str) -> bool:
    return not language.startswith("en")


def minor(language: str) -> str:
    if _fr(language):
        return "Kora Rencontres est réservé aux personnes majeures. Je ne peux pas créer de profil."
    return "Kora Dating is for adults only. I cannot create a profile."


def inappropriate(language: str) -> str:
    if _fr(language):
        return "Ta description ne peut pas être publiée telle quelle. Reformule-la simplement."
    return "Your description cannot be published as it is. Please rephrase it simply."


def ready(language: str) -> str:
    if _fr(language):
        return "Ton profil est prêt !"
    return "Your profile is ready!"


def proposal(other: Profile, language: str) -> str:
    where = f"{other.city} ({other.country})"
    if _fr(language):
        return (
            f"Voici quelqu'un qui pourrait te plaire : {other.first_name}, {other.age} ans, "
            f"{other.profession}, {where}.\n« {other.description} »\n\n"
            "Tu aimerais faire sa connaissance ? Réponds OUI ou NON."
        )
    return (
        f"Here is someone you might like: {other.first_name}, {other.age}, "
        f"{other.profession}, {where}.\n\"{other.description}\"\n\n"
        "Would you like to meet? Reply YES or NO."
    )


def nobody_yet(language: str) -> str:
    if _fr(language):
        return (
            "Je n'ai pas encore de profil qui te correspond. Dès qu'une personne "
            "compatible s'inscrit, je te la présente."
        )
    return "No matching profile yet. As soon as someone compatible joins, I will introduce them."


def yes_or_no(language: str) -> str:
    if _fr(language):
        return "Réponds simplement OUI si cette personne t'intéresse, ou NON."
    return "Just reply YES if you are interested, or NO."


def liked(language: str) -> str:
    if _fr(language):
        return "C'est noté ! Je lui présente ton profil ; si c'est réciproque, je te préviens."
    return "Noted! I am introducing your profile; if it is mutual, I will let you know."


def passed(language: str) -> str:
    return "D'accord." if _fr(language) else "All right."


def match(other: Profile, language: str) -> str:
    if _fr(language):
        return (
            f"C'est un match ! {other.first_name} ({other.age} ans, {other.city}) a dit oui "
            "aussi. Kora ne donne jamais un contact sans l'accord des deux : c'est le "
            "cas. Le déblocage du contact (1 000 FCFA) arrive très bientôt, je te "
            "préviens dès qu'il est ouvert."
        )
    return (
        f"It's a match! {other.first_name} ({other.age}, {other.city}) said yes too. "
        "Unlocking the contact (1,000 FCFA) opens very soon; I will let you know."
    )
