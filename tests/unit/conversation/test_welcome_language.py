"""A bare « Hello » is how many French speakers greet: the welcome stays French."""

from __future__ import annotations

from loka.bounded_contexts.conversation.application.services import privacy


def test_the_french_welcome_can_offer_english() -> None:
    text = privacy.welcome("fr", policy_url="https://p", english_hint=True)
    assert text.startswith("Bonjour")
    assert "You can also talk to me in English" in text


def test_the_plain_french_welcome_has_no_english_line() -> None:
    assert "English" not in privacy.welcome("fr", policy_url="https://p")


def test_hello_alone_is_a_bare_greeting() -> None:
    assert privacy.is_bare_greeting("Hello")
