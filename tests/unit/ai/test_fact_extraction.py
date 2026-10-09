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
    def test_a_bare_neighbourhood_is_not_promoted_to_a_city(
        self, extractor: FactExtractor
    ) -> None:
        """``Bastos`` is a district. Storing it as Douala's city is how a search
        for "Douala" returns a property that is not in Douala."""
        facts = extractor.validate({"location_hint": "Bastos"})

        assert not facts.has("location")
        assert facts.get("location_hint") == "Bastos"

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

        assert step is FlowStep.COLLECT_LOCATION

    def test_the_order_is_property_location_price_rooms(
        self, extractor: FactExtractor
    ) -> None:
        cases = [
            ({"property_type": "APARTMENT"}, FlowStep.COLLECT_LOCATION),
            ({"property_type": "APARTMENT", "city": "Douala"}, FlowStep.COLLECT_PRICE),
            (
                {"property_type": "APARTMENT", "city": "Douala", "price": 250_000},
                FlowStep.COLLECT_FEATURES,
            ),
            (
                {
                    "property_type": "APARTMENT",
                    "city": "Douala",
                    "price": 250_000,
                    "bedrooms": 2,
                },
                FlowStep.CONFIRM_PROPERTY,
            ),
        ]
        for entities, expected in cases:
            facts = extractor.validate(entities)
            step = extractor.next_step("CREATE_PROPERTY", facts, flow=FlowName.SUPPORT)
            assert step is expected

    def test_a_listing_intent_starts_from_what_is_already_known(
        self, extractor: FactExtractor
    ) -> None:
        """A landlord who says everything in one message must not be asked for
        the type of property they just named."""
        facts = extractor.validate(
            {"property_type": "APARTMENT", "city": "Douala", "price": 250_000}
        )

        step = extractor.next_step("CREATE_PROPERTY", facts, flow=FlowName.SUPPORT)

        assert step is FlowStep.COLLECT_FEATURES

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
            known={"property_type": "STUDIO"},
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

        assert question_key_for(step) == "ask.location"

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
