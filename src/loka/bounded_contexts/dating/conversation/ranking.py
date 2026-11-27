"""Who to propose to a member next, with all the matching knows (see ``matching``).

One place for the proposals a member asks for, the morning proposals and the
nudges: each read the same answers, faces and activity.
"""

from __future__ import annotations

from datetime import datetime, timedelta

from loka.bounded_contexts.conversation.application.plugs.turn import Steer
from loka.bounded_contexts.conversation.domain.entities.conversation_session import (
    FlowName,
    FlowStep,
)
from loka.bounded_contexts.dating.conversation.keys import LIKE_KEY, PROPOSED_KEY
from loka.bounded_contexts.dating.domain.matching import best_candidates, tastes_of
from loka.bounded_contexts.dating.domain.profile import Profile
from loka.bounded_contexts.dating.domain.store import DatingStore

MATCH_QUIET_DAYS = 7


async def ranked_for(
    store: DatingStore, member: Profile, *, now: datetime
) -> list[tuple[Profile, float]]:
    """Everyone the member could meet and has not answered, best first. Nor
    anyone who already said no to them: Dave was shown Ornela, who had passed
    on him (journal of 25 Nov 2026, 10:26)."""
    excluded = await store.answered(member.profile_id) | await store.passed_by(member.profile_id)
    members = await store.members()
    # Nor anyone with a recent match: Eva, matched with Ismaël and waiting for
    # his payment, came first for Hermann (27 Nov 2026).
    since = now - timedelta(days=MATCH_QUIET_DAYS)
    for other in members:
        if str(other.profile_id) not in excluded and await store.matched_since(
            other.profile_id, since
        ):
            excluded.add(str(other.profile_id))
    return best_candidates(
        member,
        members,
        excluded=excluded,
        refusals=await store.refusals(),
        tastes=tastes_of(members, await store.all_answers()),
        last_wrote=await store.last_wrote(),
        now=now,
    )


def proposing(other: Profile) -> Steer:
    """A proposal sent unasked: the member's next answer is a yes or a no to ``other``."""
    return Steer(
        flow=FlowName.DATING_PROFILE,
        step=FlowStep.COLLECT_CHOICE,
        question_key=LIKE_KEY,
        context={PROPOSED_KEY: str(other.profile_id), "_score": 0.0},
    )
