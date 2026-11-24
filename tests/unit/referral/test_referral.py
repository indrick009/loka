"""Referral codes as people send them, and the 10 % they earn."""

from __future__ import annotations

from urllib.parse import unquote

from loka.bounded_contexts.referral.domain.referral import (
    code_in,
    commission,
    invitation_text,
    link,
    new_code,
    only_invitation,
)


def test_a_new_code_is_read_back_from_its_own_invitation() -> None:
    code = new_code()
    assert code_in(invitation_text(code)) == code


def test_a_code_typed_in_lower_case_is_still_recognised() -> None:
    assert code_in("salut, je viens de la part de idia-7k3f2 !") == "IDIA-7K3F2"


def test_a_message_without_a_code_names_no_referrer() -> None:
    assert code_in("je cherche un studio à Bastos") is None


def test_the_prefilled_message_alone_needs_no_analysis() -> None:
    assert only_invitation(invitation_text("IDIA-7K3F2"))


def test_a_request_after_the_code_is_still_read() -> None:
    assert not only_invitation("Je viens de la part de IDIA-7K3F2, je cherche un studio")


def test_ten_percent_of_a_purchase_rounded_down() -> None:
    assert commission(1_000) == 100
    assert commission(1_505) == 150
    assert commission(0) == 0


def test_the_link_opens_idia_with_the_invitation_prefilled() -> None:
    url = link("IDIA-7K3F2", "+237 671 57 24 24")
    assert url.startswith("https://wa.me/237671572424?text=")
    assert unquote(url.split("text=", 1)[1]) == invitation_text("IDIA-7K3F2")
