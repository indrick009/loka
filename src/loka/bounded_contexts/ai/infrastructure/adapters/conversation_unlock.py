"""The two halves of the 1 000 FCFA unlock, as the conversation needs them.

Both are narrow adapters, and both exist because the conversation must not
learn another context's vocabulary:

* :class:`ConversationUnlockPayments` starts the withdrawal for one
  (tenant, listing) pair and reduces the result to the four outcomes a reply
  can be written from. The pipeline never sees a payment, a provider reference
  or a grant.
* :class:`ConversationUnlockedContacts` assembles what the fee bought — the
  landlord's number and a label for the lodging — once settlement is proven.
  It reads the landlord's own WhatsApp thread for the number, because that is
  the number the tenant is meant to call.
"""

from __future__ import annotations

import uuid
from datetime import datetime

from loka.bounded_contexts.ai.application.ports import (
    UnlockedContact,
    UnlockRequestOutcome,
)
from loka.bounded_contexts.ai.application.services.listing_recap import (
    property_type_label,
)
from loka.bounded_contexts.landlord.application.ports import LandlordDirectory
from loka.bounded_contexts.messaging.domain.repositories.conversation_session_repository import (
    ConversationSessionRepository,
)
from loka.bounded_contexts.payment.application.use_cases.initiate_service_fee import (
    InitiateServiceFeeCommand,
    InitiateServiceFeeUseCase,
)
from loka.bounded_contexts.payment.domain.exceptions import (
    AvailabilityNotConfirmed,
    PaymentAlreadySettled,
    PaymentGatewayError,
)
from loka.bounded_contexts.property.domain.repositories.property_repository import (
    PropertyRepository,
)
from loka.bounded_contexts.property.infrastructure.composition import (
    PROPERTY_REPOSITORY,
)
from loka.shared.application.unit_of_work import UnitOfWork


class ConversationUnlockPayments:
    def __init__(self, uow: UnitOfWork, *, initiate: InitiateServiceFeeUseCase) -> None:
        self._uow = uow
        self._initiate = initiate

    async def request(
        self,
        *,
        tenant_id: uuid.UUID,
        property_id: uuid.UUID,
        landlord_id: uuid.UUID,
        payer_phone: str,
        now: datetime,
    ) -> UnlockRequestOutcome:
        command = InitiateServiceFeeCommand(
            property_id=property_id,
            tenant_id=tenant_id,
            landlord_id=landlord_id,
            payer_phone=payer_phone,
        )
        try:
            await self._initiate.execute(command, now=now)
        except PaymentAlreadySettled:
            # He already holds this landlord's number and this lodging's
            # location: nothing is left to sell him, and nothing is charged.
            return UnlockRequestOutcome.ALREADY_UNLOCKED
        except AvailabilityNotConfirmed:
            # The confirmation is stale, or was never given for this listing.
            # Refusing here is the whole point of the check: no charge for a
            # contact that leads nowhere.
            return UnlockRequestOutcome.NOT_CONFIRMED
        except PaymentGatewayError:
            # The provider refused the request. Nothing is recorded as owed, so
            # the reply can honestly offer another attempt.
            return UnlockRequestOutcome.FAILED
        except Exception:
            # Provider boundary and anything unexpected: never let a broken
            # payment take down the turn. The tenant is told to try again.
            return UnlockRequestOutcome.FAILED
        return UnlockRequestOutcome.REQUESTED


class ConversationUnlockedContacts:
    def __init__(
        self,
        uow: UnitOfWork,
        *,
        sessions: ConversationSessionRepository,
        landlords: LandlordDirectory,
    ) -> None:
        self._uow = uow
        self._sessions = sessions
        self._landlords = landlords

    async def for_listing(
        self, *, property_id: uuid.UUID, landlord_id: uuid.UUID, language: str
    ) -> UnlockedContact | None:
        account_id = await self._landlords.user_id_for_profile(landlord_id)
        if account_id is None:
            return None
        # The landlord's own thread is where his WhatsApp number lives, and that
        # number is what the tenant pays to see.
        landlord_session = await self._sessions.latest_by_user(account_id)
        if landlord_session is None:
            return None

        properties: PropertyRepository = self._uow.repository(PROPERTY_REPOSITORY)
        listing = await properties.get(property_id)
        if listing is None or listing.location is None:
            return None

        return UnlockedContact(
            landlord_phone=landlord_session.phone.e164,
            lodging=_lodging_label(listing, language),
            point=_point(listing),
        )


def _lodging_label(listing: object, language: str) -> str:
    kind = property_type_label(
        getattr(getattr(listing, "property_type", None), "value", None), language
    )
    location = getattr(listing, "location", None)
    place = ", ".join(
        str(part)
        for part in (getattr(location, "neighbourhood", None), getattr(location, "city", None))
        if part
    )
    rent = getattr(getattr(listing, "rent", None), "amount", None)
    parts = [kind or "Logement", place]
    if isinstance(rent, int) and not isinstance(rent, bool):
        parts.append(f"{rent:,}".replace(",", " ") + " FCFA/mois")
    return " - ".join(part for part in parts if part)


def _point(listing: object) -> dict[str, float] | None:
    location = getattr(listing, "location", None)
    coordinates = getattr(location, "coordinates", None)
    if coordinates is None:
        return None
    return {"latitude": coordinates.latitude, "longitude": coordinates.longitude}
