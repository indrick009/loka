"""Nobody is charged before the landlord says the lodging is still free.

The business rule is one line of the spec (§9): "Avant tout paiement, Kora
demande toujours au bailleur si le logement est encore disponible. Le locataire
ne paie que si le bailleur confirme la disponibilité."

The failure it prevents is concrete: a tenant pays 1 000 FCFA, receives a
landlord's number, calls, and learns the room went last week. Kora keeps the
money and has sold nothing. The state really can change between publication
and the request, and the only person who knows is the landlord.

Payment learns nothing else about the listing. The port it declares returns a
boolean, and the catalogue's own code decides what "confirmed" means.
"""

from __future__ import annotations

import uuid

import pytest
from tests.unit.payment.fakes import (
    LANDLORD_ID,
    LISTING_ID,
    NOW,
    TENANT_ID,
    FakeAvailability,
    FakeCallbackRepository,
    FakeGateway,
    FakeGrantRepository,
    FakePaymentRepository,
    FakeUnitOfWork,
)

from loka.bounded_contexts.payment.application.use_cases.initiate_service_fee import (
    InitiateServiceFeeCommand,
    InitiateServiceFeeUseCase,
)
from loka.bounded_contexts.payment.domain.exceptions import AvailabilityNotConfirmed

pytestmark = pytest.mark.unit


def a_unit_of_work() -> FakeUnitOfWork:
    return FakeUnitOfWork(
        FakePaymentRepository(), FakeGrantRepository(), FakeCallbackRepository()
    )


def an_unlock() -> InitiateServiceFeeCommand:
    return InitiateServiceFeeCommand(
        property_id=LISTING_ID, tenant_id=TENANT_ID, landlord_id=LANDLORD_ID
    )


class TestTheGate:
    @pytest.mark.asyncio
    async def test_a_confirmed_listing_can_be_unlocked(self) -> None:
        uow = a_unit_of_work()
        gateway = FakeGateway()

        view = await InitiateServiceFeeUseCase(
            uow, gateway=gateway, availability=FakeAvailability(confirmed=True)
        ).execute(an_unlock(), now=NOW)

        assert view.status == "PENDING_CONFIRMATION"
        assert gateway.initiated

    @pytest.mark.asyncio
    async def test_an_unconfirmed_listing_is_refused_before_the_provider_is_called(
        self,
    ) -> None:
        """Refused early on purpose: a payment link created and then abandoned
        is a charge the tenant may still complete on his phone."""
        uow = a_unit_of_work()
        gateway = FakeGateway()

        with pytest.raises(AvailabilityNotConfirmed):
            await InitiateServiceFeeUseCase(
                uow, gateway=gateway, availability=FakeAvailability(confirmed=False)
            ).execute(an_unlock(), now=NOW)

        assert gateway.initiated == []
        assert uow.payments.added == []
        assert uow.commits == 0

    @pytest.mark.asyncio
    async def test_the_gate_is_asked_about_the_listing_being_unlocked(self) -> None:
        uow = a_unit_of_work()
        availability = FakeAvailability(confirmed=True)

        await InitiateServiceFeeUseCase(
            uow, gateway=FakeGateway(), availability=availability
        ).execute(an_unlock(), now=NOW)

        assert availability.asked == [LISTING_ID]

    @pytest.mark.asyncio
    async def test_a_deployment_with_no_gate_still_charges(self) -> None:
        """A missing gate must be visible in the logs, not fatal to every
        unlock: refusing all payments because a dependency is unwired is a
        worse outage than charging without the check."""
        uow = a_unit_of_work()

        view = await InitiateServiceFeeUseCase(
            uow, gateway=FakeGateway(), availability=None
        ).execute(an_unlock(), now=NOW)

        assert view.status == "PENDING_CONFIRMATION"

    @pytest.mark.asyncio
    async def test_the_refusal_names_the_pair_it_refused(self) -> None:
        uow = a_unit_of_work()

        with pytest.raises(AvailabilityNotConfirmed) as caught:
            await InitiateServiceFeeUseCase(
                uow, gateway=FakeGateway(), availability=FakeAvailability(confirmed=False)
            ).execute(an_unlock(), now=NOW)

        assert str(LISTING_ID) in str(caught.value.context)
        assert str(TENANT_ID) in str(caught.value.context)


class TestWhatPaymentDoesNotLearn:
    def test_the_port_offers_one_question_and_no_listing(self) -> None:
        """A wider port would let Payment read the catalogue, which is the
        coupling the hexagon exists to prevent."""
        from loka.bounded_contexts.payment.application.ports import ListingAvailability

        methods = {
            name
            for name in dir(ListingAvailability)
            if not name.startswith("_")
        }

        assert methods == {"is_confirmed_available"}

    def test_any_object_answering_that_question_satisfies_it(self) -> None:
        from loka.bounded_contexts.payment.application.ports import ListingAvailability

        assert isinstance(FakeAvailability(), ListingAvailability)
        assert not isinstance(uuid.uuid4(), ListingAvailability)
