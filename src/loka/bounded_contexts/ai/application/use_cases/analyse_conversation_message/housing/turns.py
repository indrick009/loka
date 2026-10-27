"""Everything the engine asks of housing during one turn.

The one door between the conversation engine and the housing rules: the
engine reads, validates, records and answers; this decides what a housing
turn *does*. Food, Dating and Found will each bring a class of this shape.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any

from loka.bounded_contexts.ai.application.ports import (
    AvailabilityAnswers,
    AvailabilityRequests,
    DemandFollowUp,
    PropertyListing,
    TenantDemands,
    UnlockPayments,
)
from loka.bounded_contexts.ai.application.services.demand_reminder import STILL_SEARCHING_KEY
from loka.bounded_contexts.ai.application.services.deterministic_classifier import (
    DeterministicClassifier,
    DeterministicMatch,
)
from loka.bounded_contexts.ai.application.services.listing_recap import listing_recap_text
from loka.bounded_contexts.ai.application.services.negotiation_copy import OFFER_QUESTION_KEY
from loka.bounded_contexts.ai.application.services.other_lodgings import NEXT_LODGING_QUESTION
from loka.bounded_contexts.ai.application.use_cases.analyse_conversation_message.flow_rules import (
    known_facts,
)
from loka.bounded_contexts.ai.application.use_cases.analyse_conversation_message.housing.availability import (  # noqa: E501
    LandlordAvailability,
)
from loka.bounded_contexts.ai.application.use_cases.analyse_conversation_message.housing.listing import (  # noqa: E501
    LandlordListing,
)
from loka.bounded_contexts.ai.application.use_cases.analyse_conversation_message.housing.media import (  # noqa: E501
    ListingMedia,
)
from loka.bounded_contexts.ai.application.use_cases.analyse_conversation_message.housing.open_question import (  # noqa: E501
    read_open_question,
)
from loka.bounded_contexts.ai.application.use_cases.analyse_conversation_message.housing.payment import (  # noqa: E501
    PAYMENT_STEPS,
    PaymentTurn,
)
from loka.bounded_contexts.ai.application.use_cases.analyse_conversation_message.housing.places import (  # noqa: E501
    one_place,
)
from loka.bounded_contexts.ai.application.use_cases.analyse_conversation_message.housing.reminder import (  # noqa: E501
    SearchReminder,
)
from loka.bounded_contexts.ai.application.use_cases.analyse_conversation_message.housing.search import (  # noqa: E501
    TenantSearch,
    while_waiting,
)
from loka.bounded_contexts.ai.application.use_cases.analyse_conversation_message.session_writer import (  # noqa: E501
    SessionWriter,
)
from loka.bounded_contexts.ai.application.use_cases.analyse_conversation_message.turn import (
    Settled,
    TurnOutcome,
    Verdict,
)
from loka.bounded_contexts.messaging.domain.entities.conversation_session import (
    ConversationSession,
    FlowStep,
)


class HousingTurns:
    def __init__(
        self,
        writer: SessionWriter,
        *,
        classifier: DeterministicClassifier,
        listing: PropertyListing | None = None,
        availability: AvailabilityAnswers | None = None,
        availability_requests: AvailabilityRequests | None = None,
        unlock_payments: UnlockPayments | None = None,
        demands: TenantDemands | None = None,
        follow_up: DemandFollowUp | None = None,
    ) -> None:
        self._writer = writer
        self._classifier = classifier
        self.media = ListingMedia(listing, writer)
        self._listing = LandlordListing(listing, self.media, writer)
        self._availability = LandlordAvailability(availability)
        self._search = TenantSearch(demands, availability_requests)
        self._payment = PaymentTurn(unlock_payments)
        self._reminder = SearchReminder(follow_up)

    def one_place(self, entities: dict[str, Any], session: ConversationSession) -> dict[str, Any]:
        return one_place(entities, session)

    def read_open_question(
        self,
        *,
        pending_key: str | None,
        hint: DeterministicMatch | None,
        intent: str,
        entities: dict[str, Any],
        text: str | None = None,
    ) -> tuple[str, dict[str, Any]]:
        return read_open_question(
            self._classifier,
            pending_key=pending_key,
            hint=hint,
            intent=intent,
            entities=entities,
            text=text,
        )

    async def settle(
        self, session: ConversationSession, *, text: str | None, intent: str, now: datetime
    ) -> Settled | None:
        """The answers read by a rule alone, before the ordinary flow."""
        pending = session.context.get("next_question")
        if pending == OFFER_QUESTION_KEY:
            settled = await self._availability.settle_offer(session, text=text, now=now)
            if settled is not None:
                return settled
        if pending == STILL_SEARCHING_KEY:
            settled = await self._reminder.settle(session, text=text, intent=intent, now=now)
            if settled is not None:
                return settled
        if session.step in PAYMENT_STEPS:
            return await self._payment.settle(session, text=text, intent=intent, now=now)
        return None

    async def apply(
        self,
        session: ConversationSession,
        verdict: Verdict,
        *,
        text: str | None,
        reply_text: str | None,
        now: datetime,
    ) -> TurnOutcome:
        """An accepted turn: act on it, move the session, pick the answer."""
        intent, facts = verdict.intent, verdict.facts
        availability_text = await self._availability.record(session, intent=intent, now=now)
        listing_text, next_step, listing_id = await self._listing.confirm(
            session, intent=intent, next_step=verdict.next_step, now=now, text=text
        )
        search_text, next_step = await self._search.record_demand(
            session, intent=intent, facts=facts, next_step=next_step, now=now
        )
        choice_text, next_step = await self._search.choose_listing(
            session, intent=intent, facts=facts, next_step=next_step, now=now
        )
        # The values the landlord is asked to confirm, as stored. Rebuilt on
        # every turn that lands here, so a correction shows at once.
        recap_text = (
            listing_recap_text(
                {**known_facts(session, intent), **facts.as_context()}, session.language
            )
            if next_step is FlowStep.CONFIRM_PROPERTY
            else None
        )
        # Read before the session moves: it is where he *was* that says his
        # message is a follow-up on a demand already waiting.
        waiting_text = while_waiting(session, intent=intent, next_step=next_step)
        question = await self._writer.advance(
            session, intent, facts, next_step, now=now, attach_property_id=listing_id
        )
        # The catalogue's numbers are data, not prose: they win over both the
        # model's sentence and the listing summary, which cannot apply to the
        # same turn.
        platform_text = (
            availability_text or search_text or waiting_text or choice_text or listing_text
        )
        reply_text = platform_text or recap_text or reply_text
        if next_step is FlowStep.PUBLISHED:
            # His other lodgings (§5): the next form opens on the spot.
            following = await self._listing.open_next_lodging(session, now=now)
            if following is not None:
                question = NEXT_LODGING_QUESTION
                reply_text = "\n".join(part for part in (reply_text, following) if part)
        return TurnOutcome(
            question=question,
            next_step=next_step,
            reply_text=reply_text,
            platform_text=platform_text,
        )

    def refused(
        self, session: ConversationSession, *, intent: str, reply_text: str | None
    ) -> TurnOutcome:
        """Not understood, but nothing he says now can be misfiled.

        The demand is kept and no question is open. Observed live with the
        model timing out: "tu me reviens quand ?" reached the rules alone and
        was told to rephrase. His criteria and the truth beat that.
        """
        platform_text = while_waiting(session, intent=intent, next_step=None)
        return TurnOutcome(
            question=None,
            next_step=None,
            reply_text=platform_text or reply_text,
            platform_text=platform_text,
        )
