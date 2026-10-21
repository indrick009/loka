"""§8 in words: the landlord's reply read by a rule, the tenant told the outcome."""

from __future__ import annotations

import pytest

from loka.bounded_contexts.ai.application.services.negotiation_copy import (
    landlord_offer_text,
    read_offer_reply,
    tenant_offer_text,
)

pytestmark = pytest.mark.unit

OFFER = {"rent_xaf": 60_000, "asked_rent_xaf": 65_000, "advance_months": 2,
         "asked_advance_months": 3}


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("oui", ("ACCEPTED", None, None)),
        ("ok d'accord mon frère", ("ACCEPTED", None, None)),
        ("non", ("REFUSED", None, None)),
        ("non je garde mon prix", ("REFUSED", None, None)),
        ("62 mil", ("COUNTERED", 62_000, None)),
        ("je peux faire 62 000", ("COUNTERED", 62_000, None)),
        ("bon 3 mois minimum", ("COUNTERED", None, 3)),
        ("63 mil et 3 mois", ("COUNTERED", 63_000, 3)),
    ],
)
def test_his_reply_is_read_by_a_rule(
    text: str, expected: tuple[str, int | None, int | None]
) -> None:
    reply = read_offer_reply(text)

    assert reply is not None
    assert (reply["outcome"], reply["rent_xaf"], reply["advance_months"]) == expected


@pytest.mark.parametrize("text", ["c'est déjà pris", "non il est loué", "hein ?", ""])
def test_anything_else_goes_to_the_ordinary_reading(text: str) -> None:
    assert read_offer_reply(text) is None


def test_the_question_names_both_sides_of_each_term() -> None:
    text = landlord_offer_text(OFFER, "fr")

    assert text.startswith("Le logement est encore disponible ? Un locataire aimerait visiter.")
    assert "60 000 FCFA par mois au lieu de 65 000" in text
    assert "2 mois d'avance au lieu de 3" in text
    assert "Répondez OUI" in text


@pytest.mark.parametrize(
    ("reply", "said"),
    [
        ({"outcome": "ACCEPTED"}, "le propriétaire accepte 60 000 FCFA par mois et 2 mois"),
        ({"outcome": "REFUSED"}, "garde ses conditions (65 000 FCFA par mois et 3 mois"),
        ({"outcome": "COUNTERED", "rent_xaf": 62_000}, "propose 62 000 FCFA par mois."),
    ],
)
def test_the_tenant_hears_the_outcome(reply: dict[str, object], said: str) -> None:
    text = tenant_offer_text(OFFER, reply, "fr")

    assert text is not None and said in text
