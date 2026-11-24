"""Nobody left to propose: the truth, and never the same sentence in a loop.

Journal of 24 Nov 2026: « Je n'ai pas encore de profil… » three times put two
members' conversations on hold.
"""

from __future__ import annotations

from loka.bounded_contexts.conversation.application.ports import Exchange
from loka.bounded_contexts.conversation.application.services.stuck import repeats_itself
from loka.bounded_contexts.dating.conversation import texts


def test_a_run_of_messages_never_gets_a_reply_held_for_repeating() -> None:
    state = texts.standing(["Joyce"], ["Aimée"], "fr")
    thread: list[Exchange] = []
    for quiet, said in enumerate(["Elle a refusé ?", "Et elle ?", "Ok", "Ok merci", "D'accord"]):
        thread.append(Exchange(speaker="user", text=said))
        reply = texts.nobody_now(state, quiet, "fr")
        assert not repeats_itself(reply, thread), (quiet, reply)
        thread.append(Exchange(speaker="idia", text=reply))


def test_the_state_says_who_is_awaited_and_never_refused() -> None:
    state = texts.standing(["Joyce"], ["Aimée"], "fr")
    assert "Joyce" in state and "j'attends sa réponse" in state
    assert "Aimée n'a pas donné suite" in state
    assert "refus" not in state
