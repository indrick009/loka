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
