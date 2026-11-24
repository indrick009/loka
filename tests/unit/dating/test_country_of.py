"""The dating country comes from the phone number: it is never asked."""

from __future__ import annotations

from loka.bounded_contexts.dating.conversation.spec import PROFILE_FORM, country_of


def test_a_cameroonian_number_lives_in_cameroon() -> None:
    assert country_of("+237672580682") == "Cameroun"


def test_a_foreign_number_gives_its_own_country() -> None:
    assert country_of("+33612345678") == "France"


def test_the_country_is_not_a_question_any_more() -> None:
    assert "country" not in {field.name for field in PROFILE_FORM}
