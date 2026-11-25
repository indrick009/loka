"""How each idia service works: what the model explains, in its own words.

Not replies: knowledge. Given to every model that talks to a user, so that
« comment ça marche ? », or a case idia does not take (a lost phone), is
explained plainly instead of guessed. Keep it true to the code: a step
described here that the platform does not do is a promise idia breaks.
"""

from __future__ import annotations

GUIDE = {
    "Documents perdus et trouvés": (
        "Only DOCUMENTS: CNI, passport, driving licence, student card, other official "
        "papers (birth certificate, diploma, car registration). Never objects: a phone, "
        "a bag, keys, money, jewellery are not handled; say so kindly and explain how "
        "documents work. Someone who finds a document declares it to idia (type, name "
        "written on it, where and when, a photo); it stays hidden from everyone. Someone "
        "who lost one declares it too; when a declared document may be theirs, they must "
        "prove it is theirs with what only the owner knows; the idia team checks the "
        "proof. Only then the owner gets the finder's number for 1 000 FCFA by Mobile "
        "Money, to arrange the handover. idia never confirms a name or that a document "
        "was found before that check."
    ),
    "Logement": (
        "A tenant gives four things: the kind of lodging, where, the maximum monthly rent, "
        "the months of advance he can pay. idia sends the lodgings that match; he picks "
        "one; idia asks the landlord whether it is still free; once confirmed, the tenant "
        "gets the landlord's number and the exact place for 1 000 FCFA by Mobile Money. "
        "Nothing is paid before the landlord confirms. A lease of a few months is an "
        "ordinary lodging: the landlord sets the minimum duration. A landlord publishes for free: "
        "kind, place, rent, conditions, photos. The landlord decides the visit, deposit "
        "and contract."
    ),
    "Rencontre": (
        "A profile: first name, age, sex, profession, city, a real photo (no filter or AI "
        "photo), a description with red flags, where to look for love. The description is "
        "read by idia only, never shown. idia proposes one person at a time, of the other "
        "sex; when both say yes, they ask each other questions through idia (10 messages "
        "and 5 photos each, 10 more for 1 000 FCFA with PLUS), numbers hidden. When both "
        "confirm, it is a match: the number is unlocked for 1 000 FCFA. Then both leave the "
        "proposals until one says it did not work out. A new photo sent replaces the "
        "profile photo."
    ),
    "Repas": (
        "Someone hungry says what they want and where: idia lists the dishes cooking today "
        "nearby, from restaurants' menus of the day. They order by the numbers; the "
        "restaurant confirms. A restaurant publishes its menu of the day on WhatsApp, as "
        "text or a photo of the menu. Publishing is FREE for restaurants for now (launch "
        "promotion): never ask a restaurant for money. Visibility and orders come from "
        "the menu itself: every hungry client nearby sees it until midnight, so publish "
        "it every day, early, with a photo of each dish."
    ),
    "Car": (
        "A traveller gives the trip and the day: idia lists the departures; they pick one, "
        "the number of seats and the seats, the passengers' names, and pay by Mobile "
        "Money; the ticket comes on WhatsApp and the agency receives the passengers."
    ),
    "Parrainage": (
        "After a first purchase, « mon lien » gives a personal link to share. Each time "
        "someone who came through it pays on idia, the referrer earns 10 % of it, for life. "
        "The credit will be spent in the idia app; nothing is paid out; at 2 000 F the "
        "idia team contacts the referrer."
    ),
    "Paiement": (
        "Always Mobile Money: idia sends a request to the phone, the person validates it "
        "on their phone. idia never asks for a code, a transfer or cash."
    ),
    "Données": (
        "« confidentialité » gives the privacy summary; « supprimer mes données » erases "
        "the history after a confirmation."
    ),
}

GUIDE_TEXT = (
    "How idia works (knowledge to explain in your own words when the user asks how "
    "something works or when the case comes up; never recite it, never promise more):\n"
    + "\n".join(f"- {service}: {how}" for service, how in GUIDE.items())
)
