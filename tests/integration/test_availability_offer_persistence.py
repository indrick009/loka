"""An availability question carrying the tenant's terms, against real PostgreSQL."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from loka.bounded_contexts.property.domain.entities.availability_check import AvailabilityCheck
from loka.bounded_contexts.property.domain.services.negotiation import (
    Offer,
    OfferOutcome,
    OfferReply,
)
from loka.bounded_contexts.property.domain.value_objects.enums import AvailabilityAnswer
from loka.bounded_contexts.property.infrastructure.persistence.availability_check_repository import (  # noqa: E501
    SqlAlchemyAvailabilityCheckRepository,
)

pytestmark = pytest.mark.integration

NOW = datetime(2026, 10, 21, 9, 0, tzinfo=UTC)
OFFER = Offer(rent_xaf=60_000, asked_rent_xaf=65_000, advance_months=2, asked_advance_months=3)


async def test_the_offer_and_the_reply_round_trip(session: AsyncSession) -> None:
    repository = SqlAlchemyAvailabilityCheckRepository(session)
    listing = uuid.uuid4()
    check = AvailabilityCheck(
        check_id=uuid.uuid4(),
        property_id=listing,
        landlord_id=uuid.uuid4(),
        tenant_id=uuid.uuid4(),
        now=NOW,
        offer=OFFER,
    )
    await repository.add(check)
    await session.commit()

    loaded = await repository.get(check.id)
    assert loaded is not None and loaded.offer == OFFER
    loaded.record_answer(
        AvailabilityAnswer.STILL_AVAILABLE,
        now=NOW,
        offer_reply=OfferReply(outcome=OfferOutcome.COUNTERED, rent_xaf=62_000),
    )
    await repository.save(loaded)
    await session.commit()

    answered = await repository.get(check.id)
    assert answered is not None
    assert answered.offer_reply == OfferReply(outcome=OfferOutcome.COUNTERED, rent_xaf=62_000)
    assert await repository.offers_made(listing) == [OFFER]
    assert await repository.asked_since(listing, since=NOW - timedelta(days=7)) == 1
    assert await repository.asked_since(listing, since=NOW + timedelta(minutes=1)) == 0
