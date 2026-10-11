"""Ask the landlord whether the lodging is still free, and apply his answer.

The rule being enforced is one sentence of the business spec (§9): **the tenant
pays only if the landlord confirms.** Two use cases hold the two halves of it.

Requesting is idempotent per listing. A second tenant interested in the same
lodging joins the question already in flight rather than sending the landlord
the same message twice, and the partial unique index makes that true even when
two requests race.

Answering is deliberately *not* idempotent. The first answer already moved the
listing — decremented it, or took it out of search — and replaying it would
cost a lodging the landlord never let go, so a second answer to the same
question is refused.

Nothing here reads a clock to decide availability. Kora never infers it: not
from a match, not from a payment, not from elapsed time. An unanswered question
simply never confirms, and the tenant is offered other lodgings instead.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import datetime

import structlog

from loka.bounded_contexts.property.domain.entities.availability_check import (
    AvailabilityCheck,
)
from loka.bounded_contexts.property.domain.repositories.property_repository import (
    AvailabilityCheckRepository,
    PropertyRepository,
)
from loka.bounded_contexts.property.domain.value_objects.enums import (
    AvailabilityAnswer,
    PropertyStatus,
)
from loka.shared.application.unit_of_work import UnitOfWork
from loka.shared.domain.errors import ConcurrencyConflict, ResourceNotFound
from loka.shared.domain.identifiers import new_id

# What the landlord is asked, verbatim from the spec. Kept here rather than in
# the outbound copy table because the wording *is* the business rule: it says a
# tenant wants to visit, and it asks nothing else.
LANDLORD_QUESTION_KEY = "ask.availability_check"


@dataclass(frozen=True, slots=True)
class RequestAvailabilityCheckCommand:
    property_id: uuid.UUID
    tenant_id: uuid.UUID | None = None


@dataclass(frozen=True, slots=True)
class AvailabilityRequest:
    """Who to ask, and about what. The outbound layer words the message."""

    check_id: uuid.UUID
    property_id: uuid.UUID
    landlord_id: uuid.UUID
    question_key: str
    already_pending: bool


@dataclass(frozen=True, slots=True)
class AnswerAvailabilityCheckCommand:
    landlord_id: uuid.UUID
    answer: AvailabilityAnswer


@dataclass(frozen=True, slots=True)
class AvailabilityOutcome:
    property_id: uuid.UUID
    tenant_id: uuid.UUID | None
    answer: str
    confirms_availability: bool
    lodgings_left: int
    listing_status: str


class RequestAvailabilityCheckUseCase:
    name = "request_availability_check"

    def __init__(
        self,
        uow: UnitOfWork,
        *,
        logger: structlog.stdlib.BoundLogger | None = None,
    ) -> None:
        self._uow = uow
        self._logger = logger or structlog.get_logger(self.name)

    async def execute(
        self, command: RequestAvailabilityCheckCommand, *, now: datetime
    ) -> AvailabilityRequest:
        properties: PropertyRepository = self._uow.repository("property")
        checks: AvailabilityCheckRepository = self._uow.repository("availability_check")

        listing = await properties.get(command.property_id)
        if listing is None:
            raise ResourceNotFound(
                "no listing to ask about",
                context={"property_id": str(command.property_id)},
            )

        pending = await checks.get_open_for_listing(command.property_id)
        if pending is not None:
            return _request(pending, already_pending=True)

        check = AvailabilityCheck(
            check_id=new_id(),
            property_id=command.property_id,
            landlord_id=listing.landlord_id,
            tenant_id=command.tenant_id,
            now=now,
        )
        try:
            await checks.add(check)
        except ConcurrencyConflict:
            # Another tenant's request won the race. Theirs is the question the
            # landlord will see, and this tenant waits on the same answer.
            existing = await checks.get_open_for_listing(command.property_id)
            if existing is None:
                raise
            return _request(existing, already_pending=True)

        self._uow.collect(check)
        await self._uow.commit()
        self._logger.info(
            "availability_check_requested",
            property_id=str(command.property_id),
            landlord_id=str(listing.landlord_id),
        )
        return _request(check, already_pending=False)


class AnswerAvailabilityCheckUseCase:
    name = "answer_availability_check"

    def __init__(
        self,
        uow: UnitOfWork,
        *,
        logger: structlog.stdlib.BoundLogger | None = None,
    ) -> None:
        self._uow = uow
        self._logger = logger or structlog.get_logger(self.name)

    async def execute(
        self, command: AnswerAvailabilityCheckCommand, *, now: datetime
    ) -> AvailabilityOutcome | None:
        """``None`` when the landlord was not asked anything.

        He may say "c'est déjà loué" out of the blue. That is worth recording
        against a listing, but there is no way to tell *which* listing from the
        message alone, so it is handed back rather than guessed at — guessing
        would take a lodging out of search on the strength of a coincidence.
        """
        checks: AvailabilityCheckRepository = self._uow.repository("availability_check")
        check = await checks.get_open_for_landlord(command.landlord_id)
        if check is None:
            self._logger.info(
                "availability_answer_without_question",
                landlord_id=str(command.landlord_id),
                answer=command.answer.value,
            )
            return None

        properties: PropertyRepository = self._uow.repository("property")
        listing = await properties.get(check.property_id)
        if listing is None:
            raise ResourceNotFound(
                "the listing this question was about no longer exists",
                context={"property_id": str(check.property_id)},
            )

        listing.answer_availability(command.answer, now=now)
        check.record_answer(command.answer, now=now)

        await properties.save(listing, expected_version=listing.persisted_version)
        await checks.save(check)
        self._uow.collect(listing)
        self._uow.collect(check)
        await self._uow.commit()

        self._logger.info(
            "availability_answered",
            property_id=str(check.property_id),
            answer=command.answer.value,
            lodgings=listing.lodgings,
            status=listing.status.value,
        )
        return AvailabilityOutcome(
            property_id=check.property_id,
            tenant_id=check.tenant_id,
            answer=command.answer.value,
            confirms_availability=check.confirms_availability,
            lodgings_left=listing.lodgings,
            listing_status=listing.status.value,
        )


class ListingAvailabilityReader:
    """Whether a listing's availability is confirmed, for contexts outside this one.

    The adapter behind Payment's own port. It answers one question and exposes
    no listing: the payment context must be able to refuse an unlock without
    learning anything about the catalogue.
    """

    def __init__(self, uow: UnitOfWork) -> None:
        self._uow = uow

    async def is_confirmed_available(self, property_id: uuid.UUID) -> bool:
        checks: AvailabilityCheckRepository = self._uow.repository("availability_check")
        answered = await checks.latest_answered_for_listing(property_id)
        if answered is None or not answered.confirms_availability:
            return False

        # The answer confirmed *then*. A listing the landlord has since marked
        # rented is not available now, and the last lodging may have gone to
        # someone else, so the listing has the final say.
        properties: PropertyRepository = self._uow.repository("property")
        listing = await properties.get(property_id)
        if listing is None:
            return False
        return listing.status is PropertyStatus.AVAILABLE and not listing.is_exhausted


def _request(check: AvailabilityCheck, *, already_pending: bool) -> AvailabilityRequest:
    return AvailabilityRequest(
        check_id=check.check_id,
        property_id=check.property_id,
        landlord_id=check.landlord_id,
        question_key=LANDLORD_QUESTION_KEY,
        already_pending=already_pending,
    )
