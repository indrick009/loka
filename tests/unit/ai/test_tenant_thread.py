"""The tenant's side of the thread, from a line number to a landlord's phone.

This is where the funnel used to end. The search printed its results, the
platform asked "indiquez le numéro du logement qui vous intéresse", the tenant
answered "2" — and nothing consumed it. He had reached the last step of the
product and the conversation simply stopped.

Three things had to exist for his number to mean anything. The session has to
remember which listings it printed, in order. The number has to bind to a
choice rather than to a price. And the choice has to reach the owner of that
lodging, which needs two translations: a listing names a landlord *profile*
while a WhatsApp thread belongs to an *account*, and the account is what holds
the phone number.

Kora promises nothing here. No reservation, no visit: only that the landlord is
being asked, because that is all that actually happens (housing spec §9).
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime
from typing import Any

import pytest

from loka.bounded_contexts.ai.application.services.deterministic_classifier import (
    DeterministicClassifier,
)
from loka.bounded_contexts.ai.domain.services.fact_extraction import (
    MAX_RESULT_CHOICES,
    FactExtractor,
)

pytestmark = pytest.mark.unit

NOW = datetime(2026, 3, 1, 9, 0, tzinfo=UTC)


class TestBindingTheTenantsNumber:
    """A bare number means whatever the open question asked for."""

    def classify(self, text: str) -> Any:
        return DeterministicClassifier().classify(text, pending="ask.search_choice")

    def test_a_line_number_is_a_choice_not_a_price(self) -> None:
        match = self.classify("2")

        assert match is not None
        assert match.intent == "COLLECT_PROPERTY_CHOICE"
        assert match.entities == {"property_choice": 2}

    def test_a_choice_costs_no_model_call(self) -> None:
        """The cheapest turn in the funnel, and the most common."""
        assert self.classify("3") is not None

    def test_a_number_past_the_list_is_handed_to_the_model(self) -> None:
        """Not bound to the nearest listing: he would be charged 1 000 FCFA for
        a lodging he never saw."""
        assert self.classify(str(MAX_RESULT_CHOICES + 1)) is None

    def test_zero_names_nothing(self) -> None:
        assert self.classify("0") is None

    def test_a_franc_amount_is_not_read_as_a_choice(self) -> None:
        assert self.classify("150000") is None


class TestTellingALandlordFromATenant:
    """Read by the rules alone when the model times out, so they must not flip."""

    @pytest.mark.parametrize(
        "text",
        [
            "Bonjour, j'ai un studio moderne à louer à Douala, je veux le mettre sur Kora",
            "J'ai une chambre à louer à Biyem-Assi",
            "Je loue mon appart de 2 chambres à Bastos",
        ],
    )
    def test_a_landlord_offering_is_not_sent_to_the_search(self, text: str) -> None:
        match = DeterministicClassifier().classify(text, pending=None)

        assert match is not None
        assert match.intent == "CREATE_PROPERTY"

    @pytest.mark.parametrize(
        "text",
        [
            "Je cherche un studio à louer à Douala",
            "Bonsoir mon frère, je cherche un studio moderne à Douala",
        ],
    )
    def test_a_tenant_is_still_a_tenant(self, text: str) -> None:
        match = DeterministicClassifier().classify(text, pending=None)

        assert match is not None
        assert match.intent == "PROPERTY_SEARCH"


class TestReadingSeveralNeighbourhoods:
    """The rules answer a location without a model, so they must keep them all."""

    def test_every_district_named_is_kept_in_order(self) -> None:
        match = DeterministicClassifier().classify(
            "Vers Bonamoussadi ou bien Makepe ça me va", pending="ask.search_location"
        )

        assert match is not None
        assert match.entities["city"] == "Douala"
        assert match.entities["neighbourhood"] == "Bonamoussadi"
        assert match.entities["neighbourhoods"] == ["Bonamoussadi", "Makepe"]

    def test_mon_frere_is_a_way_of_speaking_not_a_source(self) -> None:
        """Read as "heard of Kora through family", it swallowed the studio."""
        match = DeterministicClassifier().classify(
            "Bonsoir mon frère, je cherche un studio moderne à Douala, c'est comment ?",
            pending=None,
        )

        assert match is not None
        assert match.intent == "PROPERTY_SEARCH"
        assert match.entities["property_type"] == "STUDIO"
        assert match.entities["standing"] == "MODERN"
        assert match.entities["city"] == "Douala"

    def test_a_place_said_to_come_on_top_is_marked_as_added(self) -> None:
        match = DeterministicClassifier().classify(
            "Ah attends, si c'est à Logbessou aussi ça va", pending=None
        )

        assert match is not None
        assert match.entities["neighbourhood"] == "Logbessou"
        assert match.entities["adds_neighbourhoods"] is True

    def test_a_named_city_no_longer_hides_its_district(self) -> None:
        match = DeterministicClassifier().classify(
            "studio à Yaoundé Bastos", pending="ask.search_location"
        )

        assert match is not None
        assert match.entities["city"] == "Yaoundé"
        assert match.entities["neighbourhood"] == "Bastos"
        assert "neighbourhoods" not in match.entities


class TestValidatingTheChoice:
    @pytest.fixture
    def extractor(self) -> FactExtractor:
        return FactExtractor()

    def test_a_choice_inside_the_list_is_kept(self, extractor: FactExtractor) -> None:
        facts = extractor.validate({"property_choice": 2})

        assert facts.get("property_choice") == 2

    @pytest.mark.parametrize("choice", [0, -1, MAX_RESULT_CHOICES + 1, 99])
    def test_a_choice_outside_the_list_is_refused(
        self, extractor: FactExtractor, choice: int
    ) -> None:
        facts = extractor.validate({"property_choice": choice})

        assert not facts.has("property_choice")
        assert "property_choice" in facts.rejected


class FakeLandlordDirectory:
    """A landlord profile and the account that owns it: two different ids."""

    def __init__(self, *, account_id: uuid.UUID, profile_id: uuid.UUID) -> None:
        self.account_id = account_id
        self.profile_id = profile_id

    async def profile_id_for_user(self, user_id: uuid.UUID) -> uuid.UUID | None:
        return self.profile_id if user_id == self.account_id else None

    async def user_id_for_profile(self, landlord_id: uuid.UUID) -> uuid.UUID | None:
        return self.account_id if landlord_id == self.profile_id else None

    async def is_verified(self, landlord_id: uuid.UUID) -> bool:
        return True


class RecordingAnswerUseCase:
    """Captures the id the adapter actually looked the question up with."""

    def __init__(self) -> None:
        self.landlord_ids: list[uuid.UUID] = []

    async def execute(self, command: Any, *, now: datetime) -> None:
        self.landlord_ids.append(command.landlord_id)
        return None


class TestTheLandlordsAnswerFindsItsQuestion:
    """The bug this file exists to pin down.

    A WhatsApp reply carries the *account* that sent it. The question was
    recorded against the listing's landlord *profile*. Without translating
    between them the answer never matches anything, the use case reports "no
    question was asked", and the lodging stays in every tenant's search results
    for ever — with no error anywhere, because not-finding-a-question is a
    legitimate outcome.
    """

    @pytest.mark.asyncio
    async def test_the_account_is_translated_to_its_landlord_profile(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        from loka.bounded_contexts.ai.infrastructure.adapters import (
            conversation_availability as module,
        )

        account_id, profile_id = uuid.uuid4(), uuid.uuid4()
        recorder = RecordingAnswerUseCase()
        monkeypatch.setattr(
            module, "AnswerAvailabilityCheckUseCase", lambda uow: recorder
        )
        adapter = module.ConversationAvailabilityAnswers(
            object(),
            landlords=FakeLandlordDirectory(
                account_id=account_id, profile_id=profile_id
            ),
        )

        await adapter.record_answer(
            landlord_id=account_id, answer="AVAILABILITY_RENTED", now=NOW
        )

        assert recorder.landlord_ids == [profile_id]

    @pytest.mark.asyncio
    async def test_an_account_that_owns_no_listing_asks_nothing(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """A tenant writing "c'est déjà loué" must not reach the catalogue."""
        from loka.bounded_contexts.ai.infrastructure.adapters import (
            conversation_availability as module,
        )

        recorder = RecordingAnswerUseCase()
        monkeypatch.setattr(
            module, "AnswerAvailabilityCheckUseCase", lambda uow: recorder
        )
        adapter = module.ConversationAvailabilityAnswers(
            object(),
            landlords=FakeLandlordDirectory(
                account_id=uuid.uuid4(), profile_id=uuid.uuid4()
            ),
        )

        result = await adapter.record_answer(
            landlord_id=uuid.uuid4(), answer="AVAILABILITY_RENTED", now=NOW
        )

        assert result is None
        assert recorder.landlord_ids == []

    @pytest.mark.asyncio
    async def test_an_intent_that_is_not_an_answer_is_refused(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Guessing between "one was taken" and "all were taken" costs a
        landlord a lodging, so an unknown intent is not mapped to the nearest."""
        from loka.bounded_contexts.ai.infrastructure.adapters import (
            conversation_availability as module,
        )

        recorder = RecordingAnswerUseCase()
        monkeypatch.setattr(
            module, "AnswerAvailabilityCheckUseCase", lambda uow: recorder
        )
        adapter = module.ConversationAvailabilityAnswers(
            object(),
            landlords=FakeLandlordDirectory(
                account_id=uuid.uuid4(), profile_id=uuid.uuid4()
            ),
        )

        result = await adapter.record_answer(
            landlord_id=uuid.uuid4(), answer="CREATE_PROPERTY", now=NOW
        )

        assert result is None
        assert recorder.landlord_ids == []
