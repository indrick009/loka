"""Extracted facts must survive the domain's value objects.

These tests are the enforcement of one rule: whatever produced a candidate, the
value objects decide. They run pure, with no infrastructure, because the point
is that validation is not an infrastructure concern.
"""

from __future__ import annotations

import pytest

from loka.bounded_contexts.ai.application.services.deterministic_classifier import (
    DeterministicClassifier,
)
from loka.bounded_contexts.ai.domain.knowledge import market
from loka.bounded_contexts.ai.domain.services.fact_extraction import (
    FLOW_BY_OPENING_INTENT,
    MAX_PLAUSIBLE_RENT_XAF,
    MIN_PLAUSIBLE_RENT_XAF,
    SUPPORTED_INTENTS,
    FactExtractor,
    is_supported_intent,
    question_key_for,
)
from loka.bounded_contexts.messaging.domain.entities.conversation_session import (
    FlowName,
    FlowStep,
)

pytestmark = pytest.mark.unit


@pytest.fixture
def extractor() -> FactExtractor:
    return FactExtractor()


class TestAcceptedFacts:
    def test_a_plausible_rent_is_kept(self, extractor: FactExtractor) -> None:
        facts = extractor.validate({"price": 250_000})

        assert facts.get("rent") == 250_000
        assert facts.rejected == {}

    def test_rooms_and_surface_survive_their_bounds(self, extractor: FactExtractor) -> None:
        facts = extractor.validate(
            {"bedrooms": 3, "bathrooms": 2, "surface_area": 120}
        )

        assert facts.get("rooms") == {"bedrooms": 3, "bathrooms": 2}
        assert facts.get("surface") == 120
        assert facts.rejected == {}

    def test_a_city_builds_a_location(self, extractor: FactExtractor) -> None:
        facts = extractor.validate({"city": "Douala", "neighbourhood": " Bonapriso "})

        assert facts.get("location") == {"city": "Douala", "neighbourhood": "Bonapriso"}

    def test_a_standing_is_kept_from_either_spelling(
        self, extractor: FactExtractor
    ) -> None:
        """A model handed a French message may answer "modern": true instead of
        the enum; both are normalised to one value the form can read."""
        assert extractor.validate({"standing": "MODERNE"}).get("standing") == "MODERN"
        assert extractor.validate({"modern": True}).get("standing") == "MODERN"
        assert extractor.validate({"modern": False}).get("standing") == "NON_MODERN"
        assert extractor.validate({"standing": "ancien"}).get("standing") == "NON_MODERN"

    def test_an_unknown_standing_is_refused(self, extractor: FactExtractor) -> None:
        facts = extractor.validate({"standing": "peut-etre"})

        assert not facts.has("standing")
        assert "standing" in facts.rejected


class TestRefusedFacts:
    def test_a_negative_rent_is_refused(self, extractor: FactExtractor) -> None:
        facts = extractor.validate({"price": -5})

        assert not facts.has("rent")
        assert "rent" in facts.rejected

    def test_an_absurd_rent_is_refused(self, extractor: FactExtractor) -> None:
        facts = extractor.validate({"price": MAX_PLAUSIBLE_RENT_XAF + 1})

        assert not facts.has("rent")
        assert "rent" in facts.rejected

    def test_impossible_rooms_are_refused(self, extractor: FactExtractor) -> None:
        facts = extractor.validate({"bedrooms": 51})

        assert not facts.has("rooms")
        assert "rooms" in facts.rejected

    def test_an_impossible_surface_is_refused(self, extractor: FactExtractor) -> None:
        facts = extractor.validate({"surface_area": 2})

        assert not facts.has("surface")
        assert "surface" in facts.rejected

    def test_a_non_numeric_rent_is_refused(self, extractor: FactExtractor) -> None:
        facts = extractor.validate({"price": "beaucoup"})

        assert not facts.has("rent")
        assert "rent" in facts.rejected

    def test_a_vague_neighbourhood_is_refused(self, extractor: FactExtractor) -> None:
        facts = extractor.validate({"city": "Douala", "neighbourhood": "X"})

        assert not facts.has("location")
        assert "location" in facts.rejected

    def test_one_bad_field_does_not_discard_the_good_ones(
        self, extractor: FactExtractor
    ) -> None:
        facts = extractor.validate({"price": 250_000, "bedrooms": 99})

        assert facts.get("rent") == 250_000
        assert facts.rejected.keys() == {"rooms"}

    def test_a_refused_field_never_reaches_the_session_context(
        self, extractor: FactExtractor
    ) -> None:
        facts = extractor.validate({"price": 250_000, "bedrooms": 99})

        assert "rooms" not in facts.as_context()


class TestLocationHint:
    def test_an_unknown_neighbourhood_is_not_promoted_to_a_city(
        self, extractor: FactExtractor
    ) -> None:
        """A place the index does not know stays a hint. Promoting any word to a
        city is how a search for "Douala" returns a property that is not there."""
        facts = extractor.validate({"location_hint": "Zzzville"})

        assert not facts.has("location")
        assert facts.get("location_hint") == "Zzzville"

    def test_a_known_quartier_resolves_to_its_city(
        self, extractor: FactExtractor
    ) -> None:
        """``Bastos`` is a complete answer: the district index knows it is in
        Yaoundé, so the location question can actually close."""
        facts = extractor.validate({"location_hint": "Bastos"})

        assert facts.get("location") == {"city": "Yaoundé", "neighbourhood": "Bastos"}

    def test_a_bare_neighbourhood_without_a_city_resolves(
        self, extractor: FactExtractor
    ) -> None:
        facts = extractor.validate({"neighbourhood": "Bonapriso"})

        assert facts.get("location") == {"city": "Douala", "neighbourhood": "Bonapriso"}

    def test_an_accented_neighbourhood_matches(
        self, extractor: FactExtractor
    ) -> None:
        facts = extractor.validate({"neighbourhood": "Deido"})

        assert facts.get("location") == {"city": "Douala", "neighbourhood": "Deido"}

    def test_a_vague_hint_is_refused(self, extractor: FactExtractor) -> None:
        facts = extractor.validate({"location_hint": "-"})

        assert not facts.has("location_hint")
        assert "location_hint" in facts.rejected


class TestConversationOrder:
    def test_the_first_missing_field_decides_the_next_step(
        self, extractor: FactExtractor
    ) -> None:
        facts = extractor.validate({"property_type": "APARTMENT"})

        step = extractor.next_step("CREATE_PROPERTY", facts, flow=FlowName.SUPPORT)

        assert step is FlowStep.COLLECT_STANDING

    def test_the_form_order_is_type_standing_location_rent_then_entry_conditions(
        self, extractor: FactExtractor
    ) -> None:
        """The order matches how a listing is described here: what it is, where,
        for how much, and then the move-in cost (charges, caution, duration,
        availability, conditions) that the quality gate needs to publish."""
        cases = [
            ({"property_type": "APARTMENT"}, FlowStep.COLLECT_STANDING),
            ({"property_type": "APARTMENT", "standing": "MODERN"}, FlowStep.COLLECT_LOCATION),
            (
                {"property_type": "APARTMENT", "standing": "MODERN", "city": "Douala"},
                FlowStep.COLLECT_PRICE,
            ),
            (
                {
                    "property_type": "APARTMENT",
                    "standing": "MODERN",
                    "city": "Douala",
                    "price": 250_000,
                },
                FlowStep.COLLECT_CHARGES,
            ),
            (
                {
                    "property_type": "APARTMENT",
                    "standing": "MODERN",
                    "city": "Douala",
                    "price": 250_000,
                    "charges": 0,
                    "charging_policy": "INCLUDED",
                },
                FlowStep.COLLECT_DEPOSIT,
            ),
            (
                {
                    "property_type": "APARTMENT",
                    "standing": "MODERN",
                    "city": "Douala",
                    "price": 250_000,
                    "charges": 0,
                    "charging_policy": "INCLUDED",
                    "deposit": 250_000,
                },
                FlowStep.COLLECT_MINIMUM_DURATION,
            ),
            (
                {
                    "property_type": "APARTMENT",
                    "standing": "MODERN",
                    "city": "Douala",
                    "price": 250_000,
                    "charges": 0,
                    "charging_policy": "INCLUDED",
                    "deposit": 250_000,
                    "minimum_duration_months": 12,
                },
                FlowStep.COLLECT_AVAILABILITY,
            ),
            (
                {
                    "property_type": "APARTMENT",
                    "standing": "MODERN",
                    "city": "Douala",
                    "price": 250_000,
                    "charges": 0,
                    "charging_policy": "INCLUDED",
                    "deposit": 250_000,
                    "minimum_duration_months": 12,
                    "availability": "IMMEDIATE",
                },
                FlowStep.COLLECT_CONDITIONS,
            ),
            (
                {
                    "property_type": "APARTMENT",
                    "standing": "MODERN",
                    "city": "Douala",
                    "price": 250_000,
                    "charges": 0,
                    "charging_policy": "INCLUDED",
                    "deposit": 250_000,
                    "minimum_duration_months": 12,
                    "availability": "IMMEDIATE",
                    "conditions": "Caution deux mois, avance de trois mois.",
                },
                FlowStep.CONFIRM_PROPERTY,
            ),
        ]
        for entities, expected in cases:
            facts = extractor.validate(entities)
            step = extractor.next_step("CREATE_PROPERTY", facts, flow=FlowName.SUPPORT)
            assert step is expected, entities

    def test_a_listing_intent_starts_from_what_is_already_known(
        self, extractor: FactExtractor
    ) -> None:
        """A landlord who says everything in one message must not be asked for
        the type of property they just named."""
        facts = extractor.validate(
            {"property_type": "APARTMENT", "standing": "MODERN", "city": "Douala"}
        )

        step = extractor.next_step("CREATE_PROPERTY", facts, flow=FlowName.SUPPORT)

        assert step is FlowStep.COLLECT_PRICE

    def test_a_support_conversation_is_not_hijacked_by_a_stray_amount(
        self, extractor: FactExtractor
    ) -> None:
        """Someone asking a support question with a price in it must not suddenly
        find themselves inside a listing form."""
        facts = extractor.validate({"price": 250_000})

        step = extractor.next_step("COLLECT_PRICE", facts, flow=FlowName.SUPPORT)

        assert step is None

    def test_an_amount_advances_a_listing_that_is_already_open(
        self, extractor: FactExtractor
    ) -> None:
        facts = extractor.validate({"price": 250_000})

        step = extractor.next_step("COLLECT_PRICE", facts, flow=FlowName.PROPERTY_CREATION)

        assert step is FlowStep.COLLECT_PROPERTY_TYPE

    def test_facts_already_in_the_session_do_not_get_asked_again(
        self, extractor: FactExtractor
    ) -> None:
        """A landlord who answers the location question after naming the type
        moves to the rent question, not back to the type question."""
        facts = extractor.validate({"city": "Yaoundé"})

        step = extractor.next_step(
            "COLLECT_LOCATION",
            facts,
            flow=FlowName.PROPERTY_CREATION,
            known={"property_type": "STUDIO", "standing": "MODERN"},
        )

        assert step is FlowStep.COLLECT_PRICE

    def test_an_unrelated_intent_does_not_move_a_listing(
        self, extractor: FactExtractor
    ) -> None:
        facts = extractor.validate({"bedrooms": 2})

        step = extractor.next_step("AVAILABILITY_RENTED", facts, flow=FlowName.PROPERTY_CREATION)

        assert step is None

    def test_a_question_key_is_produced_for_the_next_step(
        self, extractor: FactExtractor
    ) -> None:
        facts = extractor.validate({"property_type": "APARTMENT"})

        step = extractor.next_step("CREATE_PROPERTY", facts, flow=FlowName.SUPPORT)

        assert question_key_for(step) == "ask.standing"

    def test_no_step_means_no_question(self) -> None:
        assert question_key_for(None) is None


class TestFlowOpening:
    def test_only_listing_intents_open_a_flow(self) -> None:
        assert FLOW_BY_OPENING_INTENT == {
            "CREATE_PROPERTY": FlowName.PROPERTY_CREATION,
            "PROPERTY_SEARCH": FlowName.PROPERTY_SEARCH,
        }

    def test_a_search_and_a_listing_open_different_flows(self) -> None:
        assert (
            FLOW_BY_OPENING_INTENT["PROPERTY_SEARCH"]
            is not FLOW_BY_OPENING_INTENT["CREATE_PROPERTY"]
        )


class TestAmountShorthand:
    def test_mil_means_thousands_not_millions(self) -> None:
        classifier = DeterministicClassifier()

        assert classifier.shorthand_amount("50 mil") == 50_000

    def test_plain_amounts_are_not_treated_as_shorthand(self) -> None:
        classifier = DeterministicClassifier()

        assert classifier.shorthand_amount("250 000 fcfa") is None
        assert classifier.shorthand_amount("250000") is None

    def test_a_shorthand_amount_is_read_as_a_price(self) -> None:
        classifier = DeterministicClassifier()

        match = classifier.classify("je vends un studio a 50 mil")

        assert match is not None
        assert match.entities["price"] == 50_000

    def test_million_beats_thousand_in_the_same_message(self) -> None:
        classifier = DeterministicClassifier()

        assert classifier.shorthand_amount("2 millions") == 2_000_000


class TestPendingAnswerBinding:
    """A short answer means the open question, never the field of the month."""

    def test_a_bare_number_answers_the_minimum_duration(self) -> None:
        classifier = DeterministicClassifier()

        match = classifier.shortcut("5", pending="ask.minimum_duration")

        assert match is not None
        assert match.intent == "COLLECT_MINIMUM_DURATION"
        assert match.entities == {"minimum_duration_months": 5}

    def test_months_wording_is_read_the_same_way(self) -> None:
        classifier = DeterministicClassifier()

        match = classifier.shortcut("5 mois", pending="ask.minimum_duration")

        assert match is not None
        assert match.entities["minimum_duration_months"] == 5

    def test_a_deposit_answer_is_not_stored_as_rent(self) -> None:
        classifier = DeterministicClassifier()

        match = classifier.classify("500 000", pending="ask.deposit")

        assert match is not None
        assert match.intent == "COLLECT_DEPOSIT"
        assert match.entities == {"deposit": 500_000}

    def test_a_short_immediate_answer_binds_to_availability(self) -> None:
        classifier = DeterministicClassifier()

        match = classifier.shortcut("immédiat", pending="ask.availability")

        assert match is not None
        assert match.entities == {"availability": "IMMEDIATE"}

    def test_a_short_date_answer_binds_to_availability(self) -> None:
        classifier = DeterministicClassifier()

        match = classifier.shortcut("01/11/2026", pending="ask.availability")

        assert match is not None
        assert match.entities == {"availability": "01/11/2026"}

    def test_a_lone_number_with_no_open_question_is_not_a_price(self) -> None:
        classifier = DeterministicClassifier()

        assert classifier.shortcut("5") is None
        assert classifier.classify("5") is None


class TestEntryConditions:
    """The move-in cost is part of the price here, so it must survive validation."""

    def test_included_charges_are_a_policy_not_an_amount(
        self, extractor: FactExtractor
    ) -> None:
        facts = extractor.validate({"charges_included": True})

        assert facts.get("charges") == 0
        assert facts.get("charging_policy") == "INCLUDED"

    def test_extra_charges_keep_their_amount(self, extractor: FactExtractor) -> None:
        facts = extractor.validate({"charges": 15_000})

        assert facts.get("charges") == 15_000
        assert facts.get("charging_policy") == "EXTRA"

    def test_a_deposit_is_kept(self, extractor: FactExtractor) -> None:
        facts = extractor.validate({"deposit": 500_000})

        assert facts.get("deposit") == 500_000

    def test_a_negative_deposit_is_refused(self, extractor: FactExtractor) -> None:
        facts = extractor.validate({"deposit": -1})

        assert not facts.has("deposit")
        assert "deposit" in facts.rejected

    def test_availability_normalises_to_a_token(self, extractor: FactExtractor) -> None:
        assert extractor.validate({"availability": "immédiatement"}).get(
            "availability"
        ) == "IMMEDIATE"
        assert extractor.validate({"availability": "01/11/2026"}).get(
            "availability"
        ) == "2026-11-01"

    def test_an_unreadable_availability_is_refused(self, extractor: FactExtractor) -> None:
        facts = extractor.validate({"availability": "un jour"})

        assert not facts.has("availability")
        assert "availability" in facts.rejected

    def test_conditions_must_be_specific_enough(self, extractor: FactExtractor) -> None:
        assert not extractor.validate({"conditions": "ok"}).has("conditions")
        kept = extractor.validate(
            {"conditions": "Caution deux mois et avance de trois mois."}
        )
        assert kept.has("conditions")


class TestMarketKnowledge:
    """The district index is the authority a bare quartier resolves against."""

    def test_a_district_resolves_to_its_city(self) -> None:
        assert market.resolve_neighbourhood("Bastos") == "Yaoundé"
        assert market.resolve_neighbourhood("Bonapriso") == "Douala"

    def test_accents_and_case_do_not_matter(self) -> None:
        assert market.resolve_city("YAOUNDE") == "Yaoundé"
        assert market.resolve_neighbourhood("deido") == "Douala"

    def test_an_unknown_place_stays_unresolved(self) -> None:
        assert market.resolve_neighbourhood("Zzzville") is None
        assert market.resolve_city("Zzzville") is None

    def test_every_entry_points_at_a_declared_source(self) -> None:
        for neighbourhood in market.NEIGHBOURHOODS:
            assert neighbourhood.source in market.SOURCES
        for expression in market.EXPRESSIONS:
            assert expression.source in market.SOURCES

    def test_a_premium_address_widens_the_price_band(self) -> None:
        base = market.price_reference("APARTMENT")
        premium = market.price_reference("APARTMENT", neighbourhood="Bastos")

        assert base is not None and premium is not None
        assert premium.low_xaf >= base.low_xaf
        assert premium.high_xaf > base.high_xaf

    def test_an_unknown_type_has_no_band(self) -> None:
        assert market.price_reference("SPACESHIP") is None


class TestTrustBoundary:
    def test_every_deterministic_intent_is_trusted(self) -> None:
        """The allowlist is the boundary between "act on this" and "do not".

        It is maintained by hand, so it is closed here by test rather than by
        memory: an intent the classifier can emit that the allowlist refuses
        would silently stop working when the pipeline is switched on.
        """
        samples = [
            "je vends un appartement a 250 000",
            "je veux publier un appartement",
            "appartement",
            "250 000 fcfa",
            "3 chambres 2 salles de bain 120m2",
            "je cherche un appartement a Douala",
            "avez-vous un studio a Bastos ?",
            "toujours disponible",
            "c'est déjà loué",
            "indisponible pour le moment",
            "j'ai trouvé via WhatsApp",
            "oui",
            "non",
            "merci",
        ]
        classifier = DeterministicClassifier()
        emitted = {
            match.intent
            for text in samples
            if (match := classifier.classify(text)) is not None
        }

        assert emitted, "the samples must produce at least one intent"
        assert emitted <= SUPPORTED_INTENTS

    def test_an_invented_intent_is_refused(self) -> None:
        assert not is_supported_intent("TRANSFER_MONEY_TO_LANDLORD")

    def test_a_real_intent_is_accepted(self) -> None:
        assert is_supported_intent("CREATE_PROPERTY")

    def test_the_search_intent_is_trusted(self) -> None:
        assert is_supported_intent("PROPERTY_SEARCH")

    def test_the_two_rent_bounds_are_ordered(self) -> None:
        assert MIN_PLAUSIBLE_RENT_XAF < MAX_PLAUSIBLE_RENT_XAF
