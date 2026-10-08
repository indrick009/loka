"""Rental application domain rules.

The aggregate owns the negotiation and decision lifecycle: one decision per
application, no counter-offer after a decision, and a confirmation only from an
accepted state. No infrastructure here.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime

import pytest

from loka.bounded_contexts.property.domain.value_objects.money import Money
from loka.bounded_contexts.rental.domain.entities.rental_application import (
    RentalApplication,
    RentalApplicationStatus,
)
from loka.shared.domain.errors import InvalidStateTransition, InvariantViolation

NOW = datetime(2026, 3, 1, 9, 0, tzinfo=UTC)
PROPERTY_ID = uuid.uuid4()
LANDLORD_ID = uuid.uuid4()
TENANT_ID = uuid.uuid4()
ACTOR_ID = uuid.uuid4()


def _application() -> RentalApplication:
    return RentalApplication(
        application_id=uuid.uuid4(),
        property_id=PROPERTY_ID,
        landlord_id=LANDLORD_ID,
        tenant_id=TENANT_ID,
        message="Bonjour, je suis intéressé.",
        now=NOW,
    )


def test_creation_records_an_interest_event() -> None:
    application = _application()
    assert application.status is RentalApplicationStatus.INTERESTED
    assert application.is_open
    events = application.pull_events()
    assert [event.event_type for event in events] == ["RentalApplicationCreated"]


def test_proposing_terms_moves_to_negotiating_and_counts_offers() -> None:
    application = _application()
    application.pull_events()
    application.propose_terms(rent=Money(150_000), deposit=Money(300_000), now=NOW)
    application.propose_terms(rent=Money(140_000), now=NOW)
    assert application.status is RentalApplicationStatus.NEGOTIATING
    assert application.offer_count == 2
    assert application.proposed_rent == Money(140_000)
    events = application.pull_events()
    assert [event.event_type for event in events] == [
        "RentalOfferProposed",
        "RentalOfferProposed",
    ]


def test_a_non_positive_rent_is_rejected() -> None:
    application = _application()
    with pytest.raises(InvariantViolation):
        application.propose_terms(rent=Money(0), now=NOW)


def test_accepting_an_open_application_records_the_decision() -> None:
    application = _application()
    application.pull_events()
    application.accept(now=NOW, actor_id=ACTOR_ID)
    assert application.status is RentalApplicationStatus.ACCEPTED
    assert application.decided_at == NOW
    assert not application.is_open
    events = application.pull_events()
    assert [event.event_type for event in events] == ["RentalApplicationAccepted"]


def test_accepting_a_decided_application_is_rejected() -> None:
    application = _application()
    application.accept(now=NOW)
    with pytest.raises(InvalidStateTransition):
        application.accept(now=NOW)


def test_negotiating_after_a_decision_is_rejected() -> None:
    application = _application()
    application.refuse(reason="not now", now=NOW)
    with pytest.raises(InvalidStateTransition):
        application.propose_terms(rent=Money(100_000), now=NOW)


def test_only_an_accepted_application_can_be_confirmed() -> None:
    application = _application()
    with pytest.raises(InvalidStateTransition):
        application.confirm(now=NOW)
    application.accept(now=NOW)
    application.confirm(now=NOW)
    assert application.status is RentalApplicationStatus.CONFIRMED
    assert application.is_terminal


def test_withdrawing_records_the_withdrawal() -> None:
    application = _application()
    application.pull_events()
    application.withdraw(now=NOW, actor_id=ACTOR_ID)
    assert application.status is RentalApplicationStatus.WITHDRAWN
    assert application.withdrawn_at == NOW
    events = application.pull_events()
    assert [event.event_type for event in events] == ["RentalApplicationWithdrawn"]


def test_a_confirmed_application_cannot_be_withdrawn() -> None:
    application = _application()
    application.accept(now=NOW)
    application.confirm(now=NOW)
    with pytest.raises(InvalidStateTransition):
        application.withdraw(now=NOW)