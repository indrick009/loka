"""The market knowledge layer stays a matter of rules, not memory.

``plausibility_window`` is the piece of §3 the conversation actually runs: it
decides what a *normal* rent looks like at a given place, so an anomaly can be
flagged without ever refusing a price a landlord is sure about. Amenities are
not covered here: the model reads the wording ("gardien", "fibre", "cour") in
any language and the extractor only validates the shape afterwards, so nothing
in this module is language-coupled.
"""

from __future__ import annotations

import pytest

from loka.bounded_contexts.ai.domain.knowledge import market
from loka.bounded_contexts.ai.domain.knowledge.market import (
    DEFAULT_PLAUSIBILITY_WINDOW,
)

pytestmark = pytest.mark.unit


class TestPlausibilityWindow:
    def test_a_known_category_uses_its_band(self) -> None:
        window = market.plausibility_window("APARTMENT")

        assert window is not None
        assert window.min_xaf == 40_000
        assert window.max_xaf == 400_000

    def test_accents_do_not_matter(self) -> None:
        clean = market.plausibility_window("Appartement")
        accented = market.plausibility_window("appartement")

        assert clean == accented

    def test_an_unknown_category_falls_back_to_the_wide_default(self) -> None:
        window = market.plausibility_window("SPACESHIP")

        assert window == DEFAULT_PLAUSIBILITY_WINDOW

    def test_a_premium_quartier_widens_but_keeps_the_floor(self) -> None:
        base = market.plausibility_window("APARTMENT")
        premium = market.plausibility_window("APARTMENT", neighbourhood="Bastos")

        assert premium is not None and base is not None
        assert premium.min_xaf == base.min_xaf
        assert premium.max_xaf == base.max_xaf * 2

    def test_the_period_scales_the_reference(self) -> None:
        window = market.plausibility_window("STUDIO", period_months=12)

        assert window is not None
        assert window.min_xaf == 20_000 * 12
        assert window.max_xaf == 150_000 * 12

    def test_a_bad_period_is_refused(self) -> None:
        with pytest.raises(ValueError):
            market.plausibility_window("STUDIO", period_months=0)

    def test_unknown_type_has_no_price_band(self) -> None:
        assert market.price_reference("SPACESHIP") is None

    def test_price_reference_matches_the_window(self) -> None:
        band = market.price_reference("APARTMENT")
        window = market.plausibility_window("APARTMENT")

        assert band is not None and window is not None
        assert band.low_xaf == window.min_xaf
        assert band.high_xaf == window.max_xaf