"""The landlord's side: registering his listing, then opening the next one."""

from __future__ import annotations

import uuid
from datetime import datetime

from loka.bounded_contexts.ai.application.ports import PropertyListing
from loka.bounded_contexts.ai.application.services.conversation_copy import (
    listing_unsaved_text,
    missing_listing_reply,
)
from loka.bounded_contexts.ai.application.services.listing_recap import kept_media_published_text
from loka.bounded_contexts.ai.application.services.other_lodgings import (
    NEXT_LODGING_QUESTION,
    NEXT_LODGINGS_KEY,
    next_lodging_facts,
    next_lodging_text,
)
from loka.bounded_contexts.ai.application.use_cases.analyse_conversation_message.housing.media import (  # noqa: E501
    ListingMedia,
)
from loka.bounded_contexts.ai.application.use_cases.analyse_conversation_message.housing.open_question import (  # noqa: E501
    is_a_question,
)
from loka.bounded_contexts.ai.application.use_cases.analyse_conversation_message.session_writer import (  # noqa: E501
    SessionWriter,
)
from loka.bounded_contexts.messaging.domain.entities.conversation_session import (
    ConversationSession,
    FlowName,
    FlowStep,
)


class LandlordListing:
    def __init__(
        self, listing: PropertyListing | None, media: ListingMedia, writer: SessionWriter
    ) -> None:
        self._listing = listing
        self._media = media
        self._writer = writer

    async def confirm(
        self,
        session: ConversationSession,
        *,
        intent: str,
        next_step: FlowStep | None,
        now: datetime,
        text: str | None = None,
    ) -> tuple[str | None, FlowStep | None, uuid.UUID | None]:
        """Register the listing when the landlord confirms the summary.

        A confirmation only means something at ``CONFIRM_PROPERTY``; anywhere
        else it is ordinary small talk and the flow is left untouched. The
        property is *not* attached here: attaching bumps the session revision
        and would break the optimistic-concurrency baseline the session writer
        captures, so the id is handed to it instead.
        """
        if (
            self._listing is None
            or intent != "AFFIRMATIVE"
            or session.step is not FlowStep.CONFIRM_PROPERTY
        ):
            return None, next_step, None
        if is_a_question(text):
            # Not a yes: the summary is shown again, with the place it holds,
            # and the confirmation is still his to give.
            return None, FlowStep.CONFIRM_PROPERTY, None

        result = await self._listing.register_from_conversation(
            user_id=session.user_id,
            facts=dict(session.context),
            existing_property_id=session.assigned_property_id,
            now=now,
        )
        if result is None:
            return listing_unsaved_text(session.language), next_step, None

        kept = await self._media.attach_kept(session, result.property_id, now=now)
        if kept is not None and kept.published and not result.published:
            # The photos he sent early were all the listing was waiting for.
            return (
                kept_media_published_text(
                    count=kept.photos, photos_only=True, language=session.language
                ),
                FlowStep.PUBLISHED,
                result.property_id,
            )
        if result.published:
            return None, FlowStep.PUBLISHED, result.property_id
        return (
            missing_listing_reply(result.missing, session.language),
            FlowStep.COLLECT_MEDIA,
            result.property_id,
        )

    async def open_next_lodging(self, session: ConversationSession, *, now: datetime) -> str | None:
        """Open the form of the next lodging he described, if any is waiting (§5).

        Type, rent and count are already known, so the form starts at the
        standing; the rest of the list waits for this one in turn.
        """
        waiting = session.context.get(NEXT_LODGINGS_KEY)
        if session.flow is not FlowName.PROPERTY_CREATION or not isinstance(waiting, list):
            return None
        lodgings = [lodging for lodging in waiting if isinstance(lodging, dict)]
        if not lodgings:
            return None
        first, rest = lodgings[0], lodgings[1:]
        observed = session.revision
        session.start_flow(
            FlowName.PROPERTY_CREATION, now=now, first_step=FlowStep.COLLECT_STANDING
        )
        session.merge_context(
            {**next_lodging_facts(first), **({NEXT_LODGINGS_KEY: rest} if rest else {})},
            now=now,
        )
        session.record_pending_question(NEXT_LODGING_QUESTION)
        await self._writer.save(session, observed=observed)
        return next_lodging_text(first, session.language)
