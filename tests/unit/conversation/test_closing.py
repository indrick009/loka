"""« ok », « bien compris »: a closing that needs no answer; « oui », « non » do."""

from __future__ import annotations

import pytest

from loka.bounded_contexts.conversation.application.services.closing import is_closing


@pytest.mark.parametrize(
    "text",
    ["Ok", "okay", "OK merci", "D'accord", "d\u2019accord merci beaucoup", "Bien compris", "Merci",
     "Ça marche", "👍", "🙏🏾", "parfait merci idia"],
)
def test_a_bare_acknowledgement_closes(text: str) -> None:
    assert is_closing(text)


@pytest.mark.parametrize(
    "text",
    ["oui", "non merci", "ok je veux un studio à Bastos", "merci, et pour un repas ?", "", "1"],
)
def test_anything_that_says_something_is_read(text: str) -> None:
    assert not is_closing(text)


@pytest.mark.parametrize(
    "text",
    [
        "noo annulé tout ça va", "annule", "laisse tomber", "oublie ça",
        "Annuler s'il te plaît", "stop",
    ],
)
def test_a_short_request_to_drop_the_form_cancels(text: str) -> None:
    from loka.bounded_contexts.conversation.application.services.closing import is_cancel

    assert is_cancel(text)


@pytest.mark.parametrize(
    "text",
    ["j'ai oublié de dire mon âge, j'ai 30 ans", "Bafoussam", "oui",
     "je veux annuler ma réservation de car pour demain matin svp merci"],
)
def test_an_answer_is_not_a_cancel(text: str) -> None:
    from loka.bounded_contexts.conversation.application.services.closing import is_cancel

    assert not is_cancel(text)
