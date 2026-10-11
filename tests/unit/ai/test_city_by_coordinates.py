"""Turning a WhatsApp pin into a city the catalogue can be searched by.

D3's eval E3 wants a shared position to produce a validated location *without
asking for the city*. A pin carries no city name, so one has to be derived, and
the only honest way to do that without a geocoding service is to ask which
known market the point falls inside.

A point outside every known market resolves to nothing. That is the whole
point: naming a city we cannot justify would publish a listing in a town the
landlord never mentioned, and asking him is cheap.
"""

from __future__ import annotations

import pytest

from loka.bounded_contexts.ai.domain.knowledge import market


class TestResolveCityByCoordinates:
    @pytest.mark.parametrize(
        ("latitude", "longitude", "expected"),
        [
            (3.8480, 11.5021, "Yaoundé"),   # city centre
            (3.8905, 11.5139, "Yaoundé"),   # Bastos
            (4.0511, 9.7679, "Douala"),     # city centre
            (4.0611, 9.7069, "Douala"),     # Bonapriso side
            (5.4737, 10.4179, "Bafoussam"),
            (4.1527, 9.2410, "Buea"),
            (9.3017, 13.3921, "Garoua"),
        ],
    )
    def test_a_pin_inside_a_known_market_names_its_city(
        self, latitude: float, longitude: float, expected: str
    ) -> None:
        assert market.resolve_city_by_coordinates(latitude, longitude) == expected

    def test_the_nearer_city_wins_when_two_markets_overlap(self) -> None:
        """Buea and Limbé are close enough that a radius alone is ambiguous."""
        assert market.resolve_city_by_coordinates(4.0228, 9.1958) == "Limbé"
        assert market.resolve_city_by_coordinates(4.1527, 9.2410) == "Buea"

    @pytest.mark.parametrize(
        ("latitude", "longitude"),
        [
            (48.8566, 2.3522),    # Paris
            (6.5244, 3.3792),     # Lagos
            (0.0, 0.0),           # Null Island
            (7.0, 12.0),          # rural Cameroon, no listed market
        ],
    )
    def test_a_pin_outside_every_known_market_names_nothing(
        self, latitude: float, longitude: float
    ) -> None:
        assert market.resolve_city_by_coordinates(latitude, longitude) is None

    def test_every_named_city_is_one_the_text_path_already_knows(self) -> None:
        """A pin and a typed name must not produce two spellings of one city.

        `resolve_city` normalises what a user types; if the coordinate table
        said "Yaounde" where the alias table says "Yaoundé", the same place
        would split the catalogue in two.
        """
        spellings = set(market.CITY_ALIASES.values())
        for centre in market.CITY_CENTRES:
            assert centre.city in spellings, centre.city

    def test_every_centre_cites_a_source(self) -> None:
        for centre in market.CITY_CENTRES:
            assert centre.source in market.SOURCES, centre.city
