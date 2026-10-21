"""His answer to "cherchez-vous toujours ?", read by a rule."""

from __future__ import annotations

import pytest

from loka.bounded_contexts.ai.application.services.demand_reminder import still_searching

pytestmark = pytest.mark.unit


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("oui", True),
        ("Oui je cherche toujours", True),
        ("ok", True),
        ("je cherche toujours mola", True),
        ("non", False),
        ("Non merci j'ai trouvé", False),
        ("c'est bon j'ai déjà trouvé", False),
        ("plus besoin", False),
        # A changed search is not a plain yes: the pipeline reads the criteria.
        ("oui mais à Bastos maintenant", None),
        ("nous verrons", None),
        ("c'est combien ?", None),
        ("", None),
    ],
)
def test_the_answer_is_read_by_a_rule(text: str, expected: bool | None) -> None:
    assert still_searching(text) is expected
