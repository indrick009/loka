"""Landlord verification flow through the real composition root.

Exercises the whole journey a tenant takes to become a publishable landlord:
request status, submit an ID card and a selfie, get a decision, then publish.
Everything runs through the production unit of work, so the profile, the
request, the evidence and the outbox event all share one transaction.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import select

from loka.bounded_contexts.landlord.application.ports import VerificationEvidence
from loka.bounded_contexts.landlord.application.use_cases.adjudicate_landlord_verification import (
    AdjudicateLandlordVerificationCommand,
    AdjudicateLandlordVerificationUseCase,
)
from loka.bounded_contexts.landlord.application.use_cases.get_landlord_verification_status import (
    LandlordStatusQuery,
)
from loka.bounded_contexts.landlord.application.use_cases.request_landlord_status import (
    RequestLandlordStatusCommand,
)
from loka.bounded_contexts.landlord.application.use_cases.review_landlord_verification import (
    ReviewDecision,
    ReviewLandlordVerificationCommand,
)
from loka.bounded_contexts.landlord.application.use_cases.submit_landlord_verification import (
    SubmitLandlordVerificationCommand,
)
from loka.bounded_contexts.landlord.domain.entities.verification_request import (
    DocumentKind,
    RejectionReason,
    VerificationStatus,
)
from loka.bounded_contexts.landlord.domain.services.verification_adjudication import (
    Adjudication,
    AdjudicationDecision,
    ReviewRoute,
)
from loka.bounded_contexts.landlord.infrastructure.persistence.models import (
    LandlordProfileRow,
    VerificationDocumentRow,
)
from loka.bounded_contexts.property.application.use_cases.publish_property import (
    PublishPropertyCommand,
)
from loka.bounded_contexts.property.domain.entities.property import Property
from loka.bounded_contexts.property.domain.entities.property_media import (
    MediaKind,
    MediaStatus,
    PropertyMedia,
)
from loka.bounded_contexts.property.domain.exceptions import PublicationBlocked
from loka.bounded_contexts.property.domain.value_objects.enums import (
    AvailabilityWindow,
    BedroomCount,
    ChargingPolicy,
    Duration,
    PropertyType,
    SurfaceArea,
)
from loka.bounded_contexts.property.domain.value_objects.location import Location
from loka.bounded_contexts.property.domain.value_objects.money import Money
from loka.shared.domain.errors import ResourceNotFound
from loka.shared.infrastructure.composition import (
    get_landlord_verification_status_use_case,
    publish_property_use_case,
    request_landlord_status_use_case,
    review_landlord_verification_use_case,
    submit_landlord_verification_use_case,
    unit_of_work,
)
from loka.shared.infrastructure.db.engine import Database
from loka.shared.infrastructure.db.outbox import OutboxRecord

pytestmark = pytest.mark.integration

NOW = datetime(2026, 3, 1, 9, 0, tzinfo=UTC)
CHECKSUM_A = "a" * 64
CHECKSUM_B = "b" * 64


async def _request_status(database: Database, user_id: uuid.UUID) -> str:
    async with unit_of_work(database) as uow:
        view = await request_landlord_status_use_case(uow).execute(
            RequestLandlordStatusCommand(user_id=user_id, display_name="Awa N."),
            now=NOW,
        )
    return view.profile_id


async def _submit(database: Database, user_id: uuid.UUID, *, risk_score: int = 10) -> str:
    async with unit_of_work(database) as uow:
        view = await submit_landlord_verification_use_case(uow).execute(
            SubmitLandlordVerificationCommand(
                user_id=user_id,
                document_kind=DocumentKind.NATIONAL_ID_CARD,
                document_object_key="identity/cni.jpg",
                document_checksum=CHECKSUM_A,
                selfie_object_key="identity/selfie.jpg",
                selfie_checksum=CHECKSUM_B,
                risk_score=risk_score,
            ),
            now=NOW,
        )
    return view.request_id


def _complete_property(landlord_id: uuid.UUID) -> Property:
    prop = Property(property_id=uuid.uuid4(), landlord_id=landlord_id, now=NOW)
    prop.set_type(PropertyType.APARTMENT, now=NOW)
    prop.set_location(Location(city="Douala", neighbourhood="Bali"), now=NOW)
    prop.set_rent(Money(150_000), now=NOW)
    prop.set_charges(Money(25_000), ChargingPolicy.EXTRA, now=NOW)
    prop.set_deposit(Money(300_000), now=NOW)
    prop.set_rooms(BedroomCount(2, 1), SurfaceArea(65), now=NOW)
    prop.set_availability(AvailabilityWindow(NOW.date() + timedelta(days=7)), now=NOW)
    prop.set_minimum_duration(Duration(6), now=NOW)
    prop.set_conditions("Refreshing", now=NOW)
    prop.add_media(
        PropertyMedia(
            media_id=uuid.uuid4(),
            object_key=f"landlords/{landlord_id}/1.jpg",
            kind=MediaKind.PHOTO,
            status=MediaStatus.UPLOADED,
        ),
        now=NOW,
    )
    return prop


async def test_a_tenant_becomes_a_publishable_landlord(database: Database) -> None:
    user_id = uuid.uuid4()
    profile_id = await _request_status(database, user_id)
    request_id = await _submit(database, user_id)

    async with unit_of_work(database) as uow:
        reviewed = await review_landlord_verification_use_case(uow).execute(
            ReviewLandlordVerificationCommand(
                request_id=uuid.UUID(request_id),
                decision=ReviewDecision.APPROVE,
                reviewer_id=uuid.uuid4(),
            ),
            now=NOW,
        )
    assert reviewed.status == VerificationStatus.VERIFIED.value
    assert reviewed.can_publish is True

    async with unit_of_work(database) as uow:
        status = await get_landlord_verification_status_use_case(uow).execute(
            LandlordStatusQuery(user_id=user_id)
        )
    assert status.profile_id == profile_id
    assert status.status == VerificationStatus.VERIFIED.value
    assert status.can_publish is True

    async with database.session() as session:
        profile = await session.get(LandlordProfileRow, uuid.UUID(profile_id))
        assert profile is not None and profile.verification_status == "VERIFIED"

    # The entitlement is real: publishing now passes the verification gate.
    prop = _complete_property(uuid.UUID(profile_id))
    async with unit_of_work(database) as uow:
        await uow.repository("property").add(prop)
        await uow.commit()
    async with unit_of_work(database) as uow:
        summary = await publish_property_use_case(uow).execute(
            PublishPropertyCommand(
                property_id=prop.id, actor_id=user_id, confirmed_by_landlord=True
            ),
            now=NOW,
        )
    assert summary.status == "AVAILABLE"


async def test_an_unverified_tenant_cannot_publish(database: Database) -> None:
    user_id = uuid.uuid4()
    profile_id = await _request_status(database, user_id)
    await _submit(database, user_id)

    prop = _complete_property(uuid.UUID(profile_id))
    async with unit_of_work(database) as uow:
        await uow.repository("property").add(prop)
        await uow.commit()

    async with unit_of_work(database) as uow:
        with pytest.raises(PublicationBlocked):
            await publish_property_use_case(uow).execute(
                PublishPropertyCommand(
                    property_id=prop.id, actor_id=user_id, confirmed_by_landlord=True
                ),
                now=NOW,
            )


async def test_a_rejected_request_does_not_unlock_publication(database: Database) -> None:
    user_id = uuid.uuid4()
    await _request_status(database, user_id)
    request_id = await _submit(database, user_id)

    async with unit_of_work(database) as uow:
        reviewed = await review_landlord_verification_use_case(uow).execute(
            ReviewLandlordVerificationCommand(
                request_id=uuid.UUID(request_id),
                decision=ReviewDecision.REJECT,
                reviewer_id=uuid.uuid4(),
                reason=RejectionReason.BLURRED_OR_UNREADABLE,
            ),
            now=NOW,
        )
    assert reviewed.status == VerificationStatus.REJECTED.value
    assert reviewed.can_publish is False

    async with unit_of_work(database) as uow:
        status = await get_landlord_verification_status_use_case(uow).execute(
            LandlordStatusQuery(user_id=user_id)
        )
    assert status.status == VerificationStatus.REJECTED.value
    assert status.can_publish is False
    assert status.latest_request is not None
    assert status.latest_request.rejection_reason == "BLURRED_OR_UNREADABLE"


async def test_the_evidence_and_events_are_persisted_together(database: Database) -> None:
    user_id = uuid.uuid4()
    await _request_status(database, user_id)
    request_id = uuid.UUID(await _submit(database, user_id))

    async with database.session() as session:
        documents = (
            await session.execute(
                select(VerificationDocumentRow).where(
                    VerificationDocumentRow.request_id == request_id
                )
            )
        ).scalars()
        assert len(list(documents)) == 1

        events = (
            await session.execute(
                select(OutboxRecord.event_type).where(
                    OutboxRecord.aggregate_id == request_id
                )
            )
        ).scalars()
        assert list(events) == ["LandlordVerificationSubmitted"]


async def test_submitting_without_requesting_status_is_not_found(database: Database) -> None:
    async with unit_of_work(database) as uow:
        with pytest.raises(ResourceNotFound):
            await submit_landlord_verification_use_case(uow).execute(
                SubmitLandlordVerificationCommand(
                    user_id=uuid.uuid4(),
                    document_kind=DocumentKind.NATIONAL_ID_CARD,
                    document_object_key="identity/cni.jpg",
                    document_checksum=CHECKSUM_A,
                    selfie_object_key="identity/selfie.jpg",
                    selfie_checksum=CHECKSUM_B,
                ),
                now=NOW,
            )


async def test_requesting_status_twice_does_not_mint_a_second_profile(
    database: Database,
) -> None:
    user_id = uuid.uuid4()
    first = await _request_status(database, user_id)
    second = await _request_status(database, user_id)

    assert first == second
    async with database.session() as session:
        profiles = (
            await session.execute(
                select(LandlordProfileRow).where(LandlordProfileRow.user_id == user_id)
            )
        ).scalars()
        assert len(list(profiles)) == 1


class _FakeUrls:
    async def url_for(self, object_key: str) -> str:
        return f"memory://{object_key}"


class _FakeAdjudicator:
    def __init__(self, decision: AdjudicationDecision, confidence: float) -> None:
        self._result = Adjudication(
            decision=decision, confidence=confidence, reason="clear"
        )

    async def adjudicate(self, evidence: VerificationEvidence) -> Adjudication:
        assert evidence.document_url.startswith("memory://")
        return self._result


async def test_a_confident_model_approval_turns_the_landlord_publishable(
    database: Database,
) -> None:
    user_id = uuid.uuid4()
    await _request_status(database, user_id)
    request_id = uuid.UUID(await _submit(database, user_id))

    async with unit_of_work(database) as uow:
        view = await AdjudicateLandlordVerificationUseCase(
            uow,
            adjudicator=_FakeAdjudicator(AdjudicationDecision.APPROVE, 0.95),
            urls=_FakeUrls(),
        ).execute(
            AdjudicateLandlordVerificationCommand(request_id=request_id), now=NOW
        )
    assert view.route == ReviewRoute.AUTO_APPROVE.value
    assert view.can_publish is True

    async with database.session() as session:
        events = (
            await session.execute(
                select(OutboxRecord.event_type).where(
                    OutboxRecord.aggregate_id == request_id
                )
            )
        ).scalars()
        assert "LandlordVerified" in list(events)

    async with unit_of_work(database) as uow:
        status = await get_landlord_verification_status_use_case(uow).execute(
            LandlordStatusQuery(user_id=user_id)
        )
    assert status.can_publish is True


async def test_an_uncertain_verdict_keeps_the_request_for_an_operator(
    database: Database,
) -> None:
    user_id = uuid.uuid4()
    await _request_status(database, user_id)
    request_id = uuid.UUID(await _submit(database, user_id))

    async with unit_of_work(database) as uow:
        view = await AdjudicateLandlordVerificationUseCase(
            uow,
            adjudicator=_FakeAdjudicator(AdjudicationDecision.UNCERTAIN, 0.4),
            urls=_FakeUrls(),
        ).execute(
            AdjudicateLandlordVerificationCommand(request_id=request_id), now=NOW
        )

    assert view.route == "MANUAL"
    assert view.can_publish is False
    async with unit_of_work(database) as uow:
        status = await get_landlord_verification_status_use_case(uow).execute(
            LandlordStatusQuery(user_id=user_id)
        )
    assert status.can_publish is False