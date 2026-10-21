"""The recap a landlord confirms: every stored value, then the question.

Observed live: asked to confirm, the model wrote "d'autres commodités ?", and
the fixed template it stood in for said "voici ce que je retiens" with nothing
after it. A "oui" to a recap nobody can see publishes whatever the session holds.
"""

from __future__ import annotations

import pytest

from loka.bounded_contexts.ai.application.services.listing_recap import listing_recap_text

pytestmark = pytest.mark.unit

FACTS = {
    "property_type": "STUDIO",
    "standing": "MODERN",
    "quantity": 1,
    "location": {"city": "Douala", "neighbourhood": "Bonamoussadi"},
    "rent": 75_000,
    "charges": 0,
    "charging_policy": "EXTRA",
    "deposit_months": 2,
    "advance_months": 6,
    "amenities": {"security": True, "parking": False},
}


def test_every_stored_value_is_shown_before_the_question() -> None:
    assert listing_recap_text(FACTS, "fr") == (
        "Voici votre annonce :\n"
        "- Studio moderne (1 logement)\n"
        "- Bonamoussadi, Douala\n"
        "- Loyer : 75 000 FCFA/mois\n"
        "- Eau et électricité : à part, selon la consommation\n"
        "- Caution : 2 mois\n"
        "- Avance : 6 mois\n"
        "- Gardien : oui\n"
        "- Parking : non\n"
        "Confirmez-vous la publication ? Répondez oui pour publier, ou "
        "dites-moi ce qu'il faut corriger."
    )


def test_an_english_thread_gets_an_english_recap() -> None:
    text = listing_recap_text({**FACTS, "quantity": 3}, "en")

    assert text.startswith("Here is your listing:\n- Modern studio (3 units)\n")
    assert "- Rent: 75 000 FCFA/month" in text


def test_a_value_not_stored_is_left_out_not_guessed() -> None:
    """An empty line would read as "none", a claim the landlord never made."""
    text = listing_recap_text({"property_type": "ROOM", "rent": 30_000}, "fr")

    assert "Caution" not in text
    assert "Gardien" not in text
    assert "- Chambre\n- Loyer : 30 000 FCFA/mois\n" in text


def test_charges_billed_on_top_show_their_amount() -> None:
    text = listing_recap_text({**FACTS, "charges": 10_000}, "fr")

    assert "Eau et électricité : 10 000 FCFA/mois en plus du loyer" in text


def test_the_photo_that_publishes_says_so() -> None:
    """The template it replaces asked for photos after the publishing one."""
    from loka.bounded_contexts.ai.application.services.listing_recap import (
        photo_received_text,
    )

    text = photo_received_text(photos=1, published=True, just_published=True, language="fr")

    assert "publiée" in text
    assert "indispensables" not in text


@pytest.mark.parametrize(
    ("photos", "published", "expected"),
    [
        (
            3,
            True,
            "Photo bien reçue (3 photos) : votre annonce est en ligne, les "
            "locataires qui cherchent un logement comme le vôtre peuvent la trouver.",
        ),
        (1, False, "Photo bien reçue (1 photo)."),
    ],
)
def test_a_later_photo_reports_the_count(photos: int, published: bool, expected: str) -> None:
    from loka.bounded_contexts.ai.application.services.listing_recap import (
        photo_received_text,
    )

    assert (
        photo_received_text(
            photos=photos, published=published, just_published=False, language="fr"
        )
        == expected
    )
