"""Development seed.

Creates a verified landlord — an account, its landlord profile and a bearer
token — and optionally a published listing so public search is not empty. The
account is upserted on the phone number, so re-running only issues a fresh token.

This is a development utility, not a product surface: production accounts are
created through the WhatsApp onboarding flow. It refuses to run when
``ENVIRONMENT`` is ``production`` so it can never mint credentials there.
"""

from __future__ import annotations

import argparse
import asyncio
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

from sqlalchemy import select

from loka.bounded_contexts.identity.domain.value_objects.phone_number import PhoneNumber
from loka.bounded_contexts.identity.infrastructure.persistence.models import (
    RefreshTokenRow,
    UserRow,
)
from loka.bounded_contexts.landlord.domain.entities.verification_request import (
    VerificationStatus,
)
from loka.bounded_contexts.landlord.infrastructure.persistence.models import LandlordProfileRow
from loka.bounded_contexts.property.domain.entities.property import Property
from loka.bounded_contexts.property.domain.entities.property_media import (
    MediaKind,
    MediaStatus,
    PropertyMedia,
)
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
from loka.bounded_contexts.property.infrastructure.composition import (
    PROPERTY_REPOSITORY,
    PROPERTY_SEARCH_PROJECTOR,
)
from loka.interfaces.http.auth import generate_token, hash_token
from loka.shared.infrastructure.composition import unit_of_work
from loka.shared.infrastructure.config.settings import Settings, get_settings
from loka.shared.infrastructure.crypto.blind_index import blind_index, derive_blind_index_key
from loka.shared.infrastructure.db.engine import Database

TOKEN_TTL = timedelta(days=30)
SEED_DEVICE_LABEL = "seed"


@dataclass(frozen=True, slots=True)
class SeedResult:
    user_id: uuid.UUID
    landlord_id: uuid.UUID
    token: str
    token_expires_at: datetime
    property_id: uuid.UUID | None = None


async def seed(
    settings: Settings,
    *,
    phone: str,
    display_name: str | None = None,
    publish_listing: bool = False,
) -> SeedResult:
    if settings.environment == "production":
        raise RuntimeError("refusing to seed accounts in production")

    now = datetime.now(UTC)
    normalized = PhoneNumber.normalize(phone)
    index_key = derive_blind_index_key(settings.encryption_key.get_secret_value())

    database = Database(settings.database, application_name="loka-seed")
    await database.connect()
    try:
        user_id, landlord_id = await _upsert_landlord(
            database,
            normalized=normalized,
            index_key=index_key,
            display_name=display_name,
            now=now,
        )
        token, expires_at = await _issue_token(database, user_id=user_id, now=now)
        property_id = (
            await _publish_sample(database, landlord_id=landlord_id, actor_id=user_id, now=now)
            if publish_listing
            else None
        )
    finally:
        await database.dispose()

    return SeedResult(
        user_id=user_id,
        landlord_id=landlord_id,
        token=token,
        token_expires_at=expires_at,
        property_id=property_id,
    )


async def _upsert_landlord(
    database: Database,
    *,
    normalized: PhoneNumber,
    index_key: bytes,
    display_name: str | None,
    now: datetime,
) -> tuple[uuid.UUID, uuid.UUID]:
    phone_hash = blind_index(normalized.e164, key=index_key)
    async with database.session() as session:
        existing = (
            await session.execute(select(UserRow).where(UserRow.phone_e164_hash == phone_hash))
        ).scalar_one_or_none()
        if existing is None:
            user_id = uuid.uuid4()
            session.add(
                UserRow(
                    id=user_id,
                    phone_e164=normalized.e164,
                    phone_e164_hash=phone_hash,
                    display_name=display_name,
                    roles=["LANDLORD", "TENANT"],
                    status="ACTIVE",
                    phone_verified_at=now,
                    locale="fr",
                    created_at=now,
                    updated_at=now,
                )
            )
        else:
            user_id = existing.id
            existing.status = "ACTIVE"
            existing.phone_verified_at = existing.phone_verified_at or now
            existing.display_name = display_name or existing.display_name
            existing.roles = sorted(set(existing.roles or []) | {"LANDLORD"})
            existing.updated_at = now

        profile = (
            await session.execute(
                select(LandlordProfileRow).where(LandlordProfileRow.user_id == user_id)
            )
        ).scalar_one_or_none()
        if profile is None:
            landlord_id = uuid.uuid4()
            session.add(
                LandlordProfileRow(
                    id=landlord_id,
                    user_id=user_id,
                    display_name=display_name,
                    verification_status=VerificationStatus.VERIFIED.value,
                    verified_at=now,
                    created_at=now,
                    updated_at=now,
                )
            )
        else:
            landlord_id = profile.id
            profile.verification_status = VerificationStatus.VERIFIED.value
            profile.verified_at = profile.verified_at or now
            profile.display_name = display_name or profile.display_name
            profile.updated_at = now

        await session.commit()
    return user_id, landlord_id


async def _issue_token(
    database: Database, *, user_id: uuid.UUID, now: datetime
) -> tuple[str, datetime]:
    raw_token = generate_token()
    expires_at = now + TOKEN_TTL
    async with database.session() as session:
        session.add(
            RefreshTokenRow(
                id=uuid.uuid4(),
                user_id=user_id,
                token_hash=hash_token(raw_token),
                device_label=SEED_DEVICE_LABEL,
                issued_at=now,
                expires_at=expires_at,
                revoked_at=None,
                is_admin_session=False,
            )
        )
        await session.commit()
    return raw_token, expires_at


async def _publish_sample(
    database: Database, *, landlord_id: uuid.UUID, actor_id: uuid.UUID, now: datetime
) -> uuid.UUID:
    prop = _sample_property(landlord_id, now=now)
    prop.publish(now=now, actor_id=actor_id)
    prop.mark_verified(now=now)
    async with unit_of_work(database) as uow:
        await uow.repository(PROPERTY_REPOSITORY).add(prop)
        await uow.commit()
    async with unit_of_work(database) as uow:
        await uow.repository(PROPERTY_SEARCH_PROJECTOR).project(prop.id)
        await uow.commit()
    return prop.id


def _sample_property(landlord_id: uuid.UUID, *, now: datetime) -> Property:
    prop = Property(property_id=uuid.uuid4(), landlord_id=landlord_id, now=now)
    prop.set_type(PropertyType.APARTMENT, now=now)
    prop.set_location(Location(city="Douala", neighbourhood="Bali"), now=now)
    prop.set_rent(Money(150_000), now=now)
    prop.set_charges(Money(25_000), ChargingPolicy.EXTRA, now=now)
    prop.set_deposit(Money(300_000), now=now)
    prop.set_rooms(BedroomCount(2, 1), SurfaceArea(65), now=now)
    prop.set_minimum_duration(Duration(6), now=now)
    prop.set_availability(AvailabilityWindow(now.date() + timedelta(days=7)), now=now)
    prop.set_conditions("Recently renovated, close to Marché de Bali", now=now)
    prop.add_media(
        PropertyMedia(
            media_id=uuid.uuid4(),
            kind=MediaKind.PHOTO,
            object_key=f"landlords/{landlord_id}/{prop.id}/living-room.jpg",
            status=MediaStatus.UPLOADED,
        ),
        now=now,
    )
    return prop


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(description="Seed a development landlord and token.")
    parser.add_argument("--phone", default="+237699000000", help="E.164 or local phone number")
    parser.add_argument("--name", dest="display_name", default="Dev Landlord")
    parser.add_argument(
        "--with-listing",
        action="store_true",
        help="also publish a sample listing and project it into search",
    )
    args = parser.parse_args(argv)

    result = asyncio.run(
        seed(
            get_settings(),
            phone=args.phone,
            display_name=args.display_name,
            publish_listing=args.with_listing,
        )
    )
    print("seed complete")
    print(f"  user_id      {result.user_id}")
    print(f"  landlord_id  {result.landlord_id}")
    if result.property_id is not None:
        print(f"  property_id  {result.property_id}")
    print(f"  token        {result.token}")
    print(f"  expires_at   {result.token_expires_at.isoformat()}")


if __name__ == "__main__":
    main()