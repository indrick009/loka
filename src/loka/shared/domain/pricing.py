"""Every price idia asks, in one place (decision of 26 Nov 2026).

Read once at start from the environment, so a price is changed in the
server's ``.env`` and takes effect at the next restart, with no code change:

    PRICES__DATING_UNLOCK_XAF   a match's number, paid by the man
    PRICES__DATING_PACK_XAF     10 more messages and 5 more photos in a talk
    PRICES__FOUND_UNLOCK_XAF    being put in touch with whoever found a document
    PRICES__LODGING_FEE_XAF     a landlord's number and the lodging's place
    PRICES__RESTAURANT_MONTH_XAF what a restaurant pays a month; 0 = free
    PRICES__AGENCY_SEAT_XAF     idia's commission per seat a travel agency sells
    PRICES__PROMO               shown next to the unlock prices (« promo -50 % »);
                                empty, nothing is shown

Every text, every prompt and every payment request reads its amount here.
"""

from __future__ import annotations

import os


def _amount(name: str, default: int) -> int:
    raw = os.environ.get(f"PRICES__{name}", "").strip()
    return int(raw) if raw.isdigit() else default


def fcfa(amount: int) -> str:
    """« 500 FCFA », « 2 000 FCFA »."""
    return f"{amount:,}".replace(",", " ") + " FCFA"


DATING_UNLOCK_XAF = _amount("DATING_UNLOCK_XAF", 500)
DATING_PACK_XAF = _amount("DATING_PACK_XAF", 500)
FOUND_UNLOCK_XAF = _amount("FOUND_UNLOCK_XAF", 500)
LODGING_FEE_XAF = _amount("LODGING_FEE_XAF", 500)
RESTAURANT_MONTH_XAF = _amount("RESTAURANT_MONTH_XAF", 0)
AGENCY_SEAT_XAF = _amount("AGENCY_SEAT_XAF", 200)
PROMO = os.environ.get("PRICES__PROMO", "promo -50 %").strip()

DATING_UNLOCK = fcfa(DATING_UNLOCK_XAF)
DATING_PACK = fcfa(DATING_PACK_XAF)
FOUND_UNLOCK = fcfa(FOUND_UNLOCK_XAF)
LODGING_FEE = fcfa(LODGING_FEE_XAF)
# The match's price as members see it, with the promotion when there is one.
DATING_UNLOCK_SHOWN = f"{DATING_UNLOCK} ({PROMO})" if PROMO else DATING_UNLOCK


def restaurant_offer(language: str = "fr") -> str:
    """What publishing costs a restaurant, as said to it."""
    if RESTAURANT_MONTH_XAF <= 0:
        if language == "en":
            return "Publishing is free during the launch promotion 🎉"
        return "La publication est gratuite pendant la promotion de lancement 🎉"
    price = fcfa(RESTAURANT_MONTH_XAF)
    if language == "en":
        return f"Publishing costs {price} a month."
    return f"La publication coûte {price} par mois."
