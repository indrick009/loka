"""Defensive SQL adapter that reads behavioural facts from other contexts.

Facts are an advisory view: a missing verification does not crash an
evaluation (a landlord with no profile is simply "PENDING"), while a missing
property has nothing to score and is reported by the caller as not found.
"""

from __future__ import annotations

import uuid
from datetime import datetime, timedelta

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from loka.bounded_contexts.fraud.domain.value_objects.facts import (
    LandlordFacts,
    PropertyFacts,
    UserFacts,
)
from loka.bounded_contexts.fraud.infrastructure.persistence.models import ReportRow
from loka.bounded_contexts.identity.infrastructure.persistence.models import UserRow
from loka.bounded_contexts.landlord.infrastructure.persistence.models import (
    LandlordProfileRow,
)
from loka.bounded_contexts.payment.infrastructure.persistence.models import (
    ServiceFeePaymentRow,
)
from loka.bounded_contexts.property.infrastructure.persistence.models import (
    PropertyMediaRow,
    PropertyRow,
)
from loka.shared.domain.errors import ResourceNotFound

OPEN_REPORT_STATUS = ("OPEN", "INVESTIGATING")


class SqlAlchemyFraudFacts:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def landlord_facts(self, landlord_id: uuid.UUID, *, now: datetime) -> LandlordFacts:
        profile = await self._session.get(LandlordProfileRow, landlord_id)
        verification = profile.verification_status if profile is not None else "PENDING"
        active_properties = await self._session.scalar(
            select(func.count())
            .select_from(PropertyRow)
            .where(PropertyRow.landlord_id == landlord_id, PropertyRow.status == "AVAILABLE")
        )
        return LandlordFacts(
            landlord_id=landlord_id,
            verification_status=verification,
            active_property_count=int(active_properties or 0),
            open_report_count=await self._open_report_count("LANDLORD", landlord_id),
        )

    async def user_facts(self, user_id: uuid.UUID, *, now: datetime) -> UserFacts:
        user = await self._session.get(UserRow, user_id)
        phone_hash = user.phone_e164_hash if user is not None else ""
        accounts = await self._session.scalar(
            select(func.count())
            .select_from(UserRow)
            .where(UserRow.phone_e164_hash == phone_hash)
        )
        cut_off = now - timedelta(days=14)
        failed = await self._session.scalar(
            select(func.count())
            .select_from(ServiceFeePaymentRow)
            .where(
                ServiceFeePaymentRow.tenant_id == user_id,
                ServiceFeePaymentRow.status == "FAILED",
                ServiceFeePaymentRow.created_at >= cut_off,
            )
        )
        return UserFacts(
            user_id=user_id,
            phone_e164_hash=phone_hash or "",
            accounts_with_same_phone=int(accounts or 0),
            failed_payments_14d=int(failed or 0),
        )

    async def property_facts(self, property_id: uuid.UUID, *, now: datetime) -> PropertyFacts:
        property_row = await self._session.get(PropertyRow, property_id)
        if property_row is None:
            raise ResourceNotFound(
                "property not found", context={"property_id": str(property_id)}
            )
        median = await self._session.scalar(
            select(
                func.percentile_cont(0.5).within_group(
                    func.coalesce(PropertyRow.public_price_xaf, PropertyRow.rent_xaf)
                )
            ).where(
                PropertyRow.city == property_row.city,
                PropertyRow.status == "AVAILABLE",
                PropertyRow.id != property_id,
            )
        )
        return PropertyFacts(
            property_id=property_id,
            landlord_id=property_row.landlord_id,
            status=property_row.status,
            city=property_row.city,
            rent_xaf=property_row.rent_xaf,
            public_price_xaf=property_row.public_price_xaf,
            other_landlords_sharing_media=await self._sharing_landlords(property_id),
            open_report_count=await self._open_report_count("PROPERTY", property_id),
            city_median_price_xaf=int(median) if median is not None else None,
        )

    async def payment_subjects(
        self, payment_id: uuid.UUID
    ) -> tuple[uuid.UUID, uuid.UUID] | None:
        row = await self._session.get(ServiceFeePaymentRow, payment_id)
        if row is None:
            return None
        return row.tenant_id, row.landlord_id

    async def _open_report_count(self, target_type: str, target_id: uuid.UUID) -> int:
        count = await self._session.scalar(
            select(func.count()).select_from(ReportRow).where(
                ReportRow.target_type == target_type,
                ReportRow.target_id == target_id,
                ReportRow.status.in_(OPEN_REPORT_STATUS),
            )
        )
        return int(count or 0)

    async def _sharing_landlords(self, property_id: uuid.UUID) -> int:
        """Distinct landlords (excluding the owner) sharing a perceptual hash."""
        mine = select(PropertyMediaRow.perceptual_hash).where(
            PropertyMediaRow.property_id == property_id,
            PropertyMediaRow.perceptual_hash.isnot(None),
        ).subquery()
        count = await self._session.scalar(
            select(func.count(func.distinct(PropertyMediaRow.owner_id))).where(
                PropertyMediaRow.perceptual_hash.in_(select(mine.c.perceptual_hash)),
                PropertyMediaRow.property_id != property_id,
            )
        )
        return int(count or 0)