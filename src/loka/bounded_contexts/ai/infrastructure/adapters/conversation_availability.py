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
from loka.bounded_contexts.property.application.use_cases.check_availability import (
    AnswerAvailabilityCheckCommand,
    AnswerAvailabilityCheckUseCase,
)
from loka.bounded_contexts.property.domain.value_objects.enums import (
    AvailabilityAnswer,
    PropertyStatus,
)
from loka.shared.application.unit_of_work import UnitOfWork

ANSWER_BY_INTENT: dict[str, AvailabilityAnswer] = {
    "AVAILABILITY_STILL_AVAILABLE": AvailabilityAnswer.STILL_AVAILABLE,
    "AVAILABILITY_ONE_TAKEN": AvailabilityAnswer.ONE_TAKEN,
    "AVAILABILITY_RENTED": AvailabilityAnswer.RENTED,
    "AVAILABILITY_TEMPORARILY_UNAVAILABLE": AvailabilityAnswer.TEMPORARILY_UNAVAILABLE,
}


def is_availability_answer(intent: str) -> bool:
    return intent in ANSWER_BY_INTENT


class ConversationAvailabilityAnswers:
    def __init__(self, uow: UnitOfWork) -> None:
        self._uow = uow

    async def record_answer(
        self, *, landlord_id: uuid.UUID, answer: str, now: datetime
    ) -> AvailabilityAnswerResult | None:
        domain_answer = ANSWER_BY_INTENT.get(answer)
        if domain_answer is None:
            return None

        outcome = await AnswerAvailabilityCheckUseCase(self._uow).execute(
            AnswerAvailabilityCheckCommand(
                landlord_id=landlord_id, answer=domain_answer
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
