"""The tenant's side: recording his demand, then the lodging he picks."""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any

from loka.bounded_contexts.ai.application.ports import AvailabilityRequests, TenantDemands
from loka.bounded_contexts.ai.application.services.conversation_copy import (
    availability_pending_text,
    choice_out_of_range_text,
    demand_recorded_text,
    demand_waiting_text,
    search_query,
    search_unavailable_text,
)
from loka.bounded_contexts.ai.application.services.match_proposals import SHOWN_LISTINGS_KEY
from loka.bounded_contexts.ai.application.use_cases.analyse_conversation_message.flow_rules import (
    known_facts,
    opens_new_flow,
)
from loka.bounded_contexts.ai.domain.services.fact_extraction import (
    SEARCH_REQUIREMENTS,
    ExtractedFacts,
)
from loka.bounded_contexts.messaging.domain.entities.conversation_session import (
    ConversationSession,
    FlowStep,
)


def nth_listing(shown: Any, choice: int) -> uuid.UUID | None:
    """The listing behind a one-based line number, or ``None``.

    Defensive about the stored value because it comes back out of a JSON
    column: a malformed context must leave the tenant with a question, never a
    crash and never the wrong lodging.
    """
    if not isinstance(shown, list) or not 1 <= choice <= len(shown):
        return None
    try:
        return uuid.UUID(str(shown[choice - 1]))
    except (ValueError, AttributeError, TypeError):
        return None


def while_waiting(
    session: ConversationSession, *, intent: str, next_step: FlowStep | None
) -> str | None:
    """The platform's answer to a tenant writing again while his demand waits.

    Only when the turn changed nothing: a new criterion re-records the demand
    and is acknowledged as such, and opening another flow is its own answer.
    Everything else — "tu me reviens quand ?", "merci", "do you have something
    now?" — gets his criteria back and the truth that nothing is ready yet,
    never a model's sentence promising a delay or a visit.
    """
    if session.step is not FlowStep.AWAITING_MATCH or next_step is not None:
        return None
    if opens_new_flow(session, intent):
        return None
    query = search_query(session.context)
    if query is None:
        return None
    return demand_waiting_text(query, session.language)


class TenantSearch:
    def __init__(
        self,
        demands: TenantDemands | None,
        availability_requests: AvailabilityRequests | None,
    ) -> None:
        self._demands = demands
        self._availability_requests = availability_requests

    async def record_demand(
        self,
        session: ConversationSession,
        *,
        intent: str,
        facts: ExtractedFacts,
        next_step: FlowStep | None,
        now: datetime,
    ) -> tuple[str | None, FlowStep | None]:
        """Take the tenant's search and promise nothing.

        Kora does not answer a search in the thread. It records the demand and
        says so — whether a lodging matches or not — because a direct match, a
        neighbouring area or a price a little above all need preparing, and
        §8's negotiation with a landlord cannot happen in the time a reply
        takes. Only ``PRESENT_RESULTS`` reaches this, which is the step the
        extractor hands over exactly when the tenant's form is filled.
        """
        if next_step is not FlowStep.PRESENT_RESULTS:
            return None, next_step
        if session.step is FlowStep.COLLECT_PROPERTY_CHOICE and isinstance(
            facts.get("property_choice"), int
        ):
            # His "1" picks a lodging he was sent; it is not his search again.
            # Read as one, it re-opened the demand that list had just answered,
            # was matched again and sent him the same list a second time.
            return None, next_step
        stated = facts.as_context()
        if session.step is FlowStep.AWAITING_AVAILABILITY and not any(
            name in stated and stated[name] != session.context.get(name)
            for name, _ in SEARCH_REQUIREMENTS
        ):
            # Same trap one step later. Observed live: his lodging was already
            # put to its owner, "ok je prends le premier" re-recorded the
            # demand and the same lodging was proposed to him again. Only a new
            # criterion is a new search; anything else waits on the owner.
            return availability_pending_text(session.language), FlowStep.AWAITING_AVAILABILITY
        if self._demands is None or session.user_id is None:
            # Nowhere to keep the demand, so nothing to come back to: a
            # deployment that never wired the port fails as a sentence, not as
            # a model reply promising a follow-up.
            return search_unavailable_text(session.language), next_step

        query = search_query({**known_facts(session, intent), **facts.as_context()})
        if query is None:
            # The form reported complete but the facts name no place to search.
            # Acknowledging would promise a follow-up on a request nobody can
            # act on, so he is asked which criterion to give instead.
            return search_unavailable_text(session.language), FlowStep.COLLECT_SEARCH_CRITERIA

        recorded = await self._demands.record(tenant_id=session.user_id, query=query, now=now)
        if not recorded:
            return search_unavailable_text(session.language), FlowStep.COLLECT_SEARCH_CRITERIA
        return demand_recorded_text(session.language), FlowStep.AWAITING_MATCH

    async def choose_listing(
        self,
        session: ConversationSession,
        *,
        intent: str,
        facts: ExtractedFacts,
        next_step: FlowStep | None,
        now: datetime,
    ) -> tuple[str | None, FlowStep | None]:
        """Turn the tenant's line number into a question for the landlord.

        This is the step the thread used to die on: the list was printed, the
        number was asked for, the answer was understood — and nothing consumed
        it. Kora says nothing about a reservation and promises no visit, only
        that the landlord is being asked, because that is the only thing that
        actually happens (housing spec §9).

        A number naming nothing is re-asked rather than resolved to the nearest
        listing: charging 1 000 FCFA for a lodging the tenant never saw is
        worse than one more question. The one exception is a *single* result —
        there, "je prends" names the only lodging there is.
        """
        # Keyed on where the session *is*, not on where the form recompute
        # would go: ``next_step`` walks the tenant's criteria again and would
        # send him back to the type question, which is not what answering a
        # choice means.
        if session.step is not FlowStep.COLLECT_PROPERTY_CHOICE:
            return None, next_step
        shown = session.context.get(SHOWN_LISTINGS_KEY)
        choice = facts.get("property_choice")
        if not isinstance(choice, int) or isinstance(choice, bool):
            if intent == "AFFIRMATIVE" and isinstance(shown, list) and len(shown) == 1:
                choice = 1
            else:
                return None, FlowStep.COLLECT_PROPERTY_CHOICE
        if self._availability_requests is None:
            return None, FlowStep.COLLECT_PROPERTY_CHOICE

        listing_id = nth_listing(shown, choice)
        if listing_id is None:
            # Either he answered a list he was never shown, or the number is
            # past its end. Both leave him on the same step, asked again.
            return choice_out_of_range_text(session.language), FlowStep.COLLECT_PROPERTY_CHOICE

        asked = await self._availability_requests.request(
            property_id=listing_id, tenant_id=session.user_id, now=now
        )
        if not asked:
            return choice_out_of_range_text(session.language), FlowStep.COLLECT_PROPERTY_CHOICE
        return availability_pending_text(session.language), FlowStep.AWAITING_AVAILABILITY
