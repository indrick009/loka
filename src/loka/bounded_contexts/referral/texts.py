"""Every sentence referral writes itself."""

from __future__ import annotations

from loka.bounded_contexts.referral.domain.referral import (
    CONTACT_THRESHOLD_XAF,
    RATE_PERCENT,
)


def _f(amount: int) -> str:
    return f"{amount:,}".replace(",", " ") + " F"


def not_yet() -> str:
    return (
        "Le parrainage s'ouvre après ton premier achat sur idia 🎁 Ensuite, tu "
        f"pourras inviter tes proches et gagner {RATE_PERCENT} % de chacun de leurs achats."
    )


def your_link(link: str, referees: int, earned: int) -> str:
    people = "1 filleul" if referees == 1 else f"{referees} filleuls"
    return (
        f"🎁 Ton lien de parrainage :\n{link}\n\n"
        f"Chaque fois qu'une personne arrivée par ce lien paie sur idia, tu gagnes "
        f"{RATE_PERCENT} % de son achat, à vie.\n\n"
        f"Tes gains : {_f(earned)} ({people}). Ils seront utilisables dans l'app idia. "
        f"Une fois {_f(CONTACT_THRESHOLD_XAF)} atteints, l'assistance idia te contactera.\n\n"
        "Transfère le message ci-dessous à tes contacts 👇"
    )


def to_forward(link: str) -> str:
    return (
        "Je te recommande *idia* 👋 l'assistant WhatsApp qui t'aide à trouver un logement, "
        "un repas, un billet de car, l'amour, ou à retrouver un objet perdu.\n\n"
        f"Clique ici pour lui écrire : {link}"
    )


def newcomer() -> str:
    return (
        "🎉 Une nouvelle personne a rejoint idia grâce à ton lien. Tu gagneras "
        f"{RATE_PERCENT} % de chacun de ses achats."
    )


def credited(amount: int, earned: int) -> str:
    text = (
        f"🎁 Une personne que tu as parrainée vient de payer sur idia : +{_f(amount)}.\n"
        f"Tes gains : {_f(earned)}."
    )
    if earned >= CONTACT_THRESHOLD_XAF:
        text += f"\nTu as atteint {_f(CONTACT_THRESHOLD_XAF)} : l'assistance idia va te contacter."
    return text


def threshold_reached(phone: str, earned: int) -> str:
    return f"🎁 Parrainage : {phone} a atteint {_f(earned)} de gains. À contacter."
