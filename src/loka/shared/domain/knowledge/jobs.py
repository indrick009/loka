"""Cameroonian jobs a model may not know, as people name them on WhatsApp.

Read by the models that note what someone does for a living (the dating
profile): « je suis benskineur » is a job, kept in the member's own word, and
never asked again nor taken for a name or a place.
"""

from __future__ import annotations

CAMEROON_JOBS: dict[str, str] = {
    "benskineur / bensikineur": "moto-taxi driver",
    "moto-taximan / mototaximan": "moto-taxi driver",
    "taximan": "taxi driver",
    "clandoman / chauffeur de clando": "driver of an unofficial taxi",
    "bayam-sellam": "market reseller who buys wholesale and sells retail",
    "call-boxeur / call-boxeuse": "street seller of phone calls and mobile credit",
    "sauveteur": "street hawker",
    "pousseur": "porter pushing a hand cart at the market",
    "docker": "port or market loader",
    "braiseur / braiseuse": "street grill cook (fish, soya, maize)",
    "vendeuse de beignets / beignetariat": "doughnut and beans seller",
    "tenancier de tourne-dos / circuit": "runs a small popular eatery",
    "tresseuse": "hair braider",
    "couturier / couturière / tailleur": "tailor or dressmaker",
    "cordonnier": "shoe repairer",
    "réparateur / technicien de téléphones": "phone repairer",
    "vigile / gardien": "security guard",
    "ménagère / ménagère de maison": "housekeeper",
    "commerçant / débrouillard": "trader, self-employed",
    "fonctionnaire": "civil servant",
    "vacataire": "part-time contract teacher",
    "agent de transfert / OM-MoMo": "mobile money agent",
    "feymen (péjoratif)": "con man: never a job to record as given, ask what they do",
}


def jobs_glossary() -> str:
    """One line a prompt can carry: « word = meaning; … »."""
    return "; ".join(f"{word} = {meaning}" for word, meaning in CAMEROON_JOBS.items())
