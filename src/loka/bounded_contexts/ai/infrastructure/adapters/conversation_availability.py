"""A landlord's availability answer, carried from a WhatsApp thread to a listing.

The conversation knows a landlord said "un studio a été pris". It does not know
which listing he meant, because a WhatsApp reply names none. The question he
was asked does, and finding it is this adapter's whole job — the pipeline is
left depending on the act of recording an answer and nothing else.

The intent names and the domain's answers are kept apart deliberately. The
pipeline speaks in intents the model may propose; the catalogue speaks in
:class:`AvailabilityAnswer`. An intent this adapter does not recognise is
refused rather than mapped to the nearest one: guessing between "one was
taken" and "all were taken" costs a landlord a lodging.
"""

from __future__ import annotations

import uuid
from datetime import datetime

from loka.bounded_contexts.ai.application.ports import AvailabilityAnswerResult
from loka.bounded_contexts.landlord.application.ports import LandlordDirectory
from loka.bounded_contexts.property.application.use_cases.check_availability import (
    AnswerAvailabilityCheckCommand,
    AnswerAvailabilityCheckUseCase,
    RequestAvailabilityCheckCommand,
    RequestAvailabilityCheckUseCase,
)
from loka.bounded_contexts.property.domain.value_objects.enums import (
    AvailabilityAnswer,
    PropertyStatus,
)
from loka.shared.application.unit_of_work import UnitOfWork
from loka.shared.domain.errors import ResourceNotFound

ANSWER_BY_INTENT: dict[str, AvailabilityAnswer] = {
    "AVAILABILITY_STILL_AVAILABLE": AvailabilityAnswer.STILL_AVAILABLE,
    "AVAILABILITY_ONE_TAKEN": AvailabilityAnswer.ONE_TAKEN,
    "AVAILABILITY_RENTED": AvailabilityAnswer.RENTED,
    "AVAILABILITY_TEMPORARILY_UNAVAILABLE": AvailabilityAnswer.TEMPORARILY_UNAVAILABLE,
}


def is_availability_answer(intent: str) -> bool:
    return intent in ANSWER_BY_INTENT


class ConversationAvailabilityAnswers:
    def __init__(self, uow: UnitOfWork, *, landlords: LandlordDirectory) -> None:
        self._uow = uow
        self._landlords = landlords

    async def record_answer(
        self, *, landlord_id: uuid.UUID, answer: str, now: datetime
    ) -> AvailabilityAnswerResult | None:
        domain_answer = ANSWER_BY_INTENT.get(answer)
        if domain_answer is None:
            return None

        # ``landlord_id`` here is the *account* that sent the WhatsApp message.
        # A listing names a landlord *profile*, and so does the question asked
        # about it, so without this translation the answer never finds its
        # question and the lodging stays in search for ever.
        profile_id = await self._landlords.profile_id_for_user(landlord_id)
        if profile_id is None:
            return None

        outcome = await AnswerAvailabilityCheckUseCase(self._uow).execute(
            AnswerAvailabilityCheckCommand(
                landlord_id=profile_id, answer=domain_answer
            ),
            now=now,
        )
        if outcome is None:
            return None
        return AvailabilityAnswerResult(
            confirms_availability=outcome.confirms_availability,
            lodgings_left=outcome.lodgings_left,
            delisted=outcome.listing_status != PropertyStatus.AVAILABLE.value,
        )


class ConversationAvailabilityRequests:
    """Starts the landlord's question for a listing a tenant picked.

    Returns a bool and no listing: the conversation has no business learning
    who the landlord is, and the question reaches him through the outbound
    layer rather than through the tenant's thread.

    A listing that has vanished between the search and the choice is a
    ``False`` rather than an exception — the tenant is told his number names
    nothing, which is true and actionable, instead of seeing a failure.
    """

    def __init__(self, uow: UnitOfWork) -> None:
        self._uow = uow

    async def request(
        self, *, property_id: uuid.UUID, tenant_id: uuid.UUID | None, now: datetime
    ) -> bool:
        try:
            await RequestAvailabilityCheckUseCase(self._uow).execute(
                RequestAvailabilityCheckCommand(
                    property_id=property_id, tenant_id=tenant_id
                ),
                now=now,
            )
        except ResourceNotFound:
            return False
        return True
