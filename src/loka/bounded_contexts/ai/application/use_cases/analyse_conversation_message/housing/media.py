"""Photos and videos of a lodging, read without a model.

Housing step 9 makes photos mandatory, so this is the step the whole media
pipeline exists to serve. There is nothing to classify: the bytes are already
stored and the kind is already known.
"""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import TYPE_CHECKING

from loka.bounded_contexts.ai.application.ports import PropertyListing
from loka.bounded_contexts.ai.application.services.listing_recap import (
    media_kept_text,
    media_received_text,
    media_refused_text,
    unsupported_media_text,
)
from loka.bounded_contexts.ai.application.use_cases.analyse_conversation_message.housing.keys import (  # noqa: E501
    PENDING_MEDIA_KEY,
)
from loka.bounded_contexts.ai.application.use_cases.analyse_conversation_message.session_writer import (  # noqa: E501
    SessionWriter,
)
from loka.bounded_contexts.ai.application.use_cases.analyse_conversation_message.turn import (
    Reading,
)
from loka.bounded_contexts.ai.domain.services.fact_extraction import LISTING_FLOWS
from loka.bounded_contexts.messaging.domain.entities.conversation_session import (
    ConversationSession,
    FlowName,
    FlowStep,
)
from loka.bounded_contexts.messaging.domain.value_objects.inbound_message import MessageKind
from loka.shared.domain.errors import DomainError
from loka.shared.infrastructure.logging import get_logger

if TYPE_CHECKING:
    from loka.bounded_contexts.housing.application.use_cases.create_property_from_conversation import (  # noqa: E501
        ConversationMediaResult,
    )

_logger = get_logger(__name__)

# What each WhatsApp kind becomes on a listing. A voice note or a sticker is
# absent on purpose: both used to be filed as photos of the lodging.
_LISTING_MEDIA: dict[MessageKind, str] = {
    MessageKind.IMAGE: "PHOTO",
    MessageKind.VIDEO: "VIDEO",
    MessageKind.DOCUMENT: "DOCUMENT",
}

# What the rewriter is told the user sent when there is no caption: told
# "(photo)" for a video, it wrote back that his photo was received.
_MEDIA_MARKERS: dict[MessageKind, str] = {
    MessageKind.IMAGE: "(photo envoyée)",
    MessageKind.VIDEO: "(vidéo envoyée)",
    MessageKind.DOCUMENT: "(document envoyé)",
    MessageKind.AUDIO: "(note vocale envoyée)",
    MessageKind.STICKER: "(sticker envoyé)",
}


def media_marker(kind: MessageKind) -> str:
    return _MEDIA_MARKERS.get(kind, "(fichier envoyé)")


def refused_media(session: ConversationSession, *, kind: MessageKind, refused: str) -> Reading:
    """Too heavy to store — the gateway still forwarded it so he hears why,
    instead of a video that vanished without a word."""
    return Reading(
        intent="UNKNOWN",
        confidence=1.0,
        reason="media_refused",
        reply_text=media_refused_text(refused, session.language),
        media_marker=media_marker(kind),
    )


class ListingMedia:
    def __init__(self, listing: PropertyListing | None, writer: SessionWriter) -> None:
        self._listing = listing
        self._writer = writer

    async def read(
        self,
        session: ConversationSession,
        *,
        message_id: uuid.UUID,
        object_key: str,
        kind: MessageKind,
        now: datetime,
    ) -> Reading:
        """Attach what the user sent to the listing the conversation is building.

        A photo sent before the listing exists is kept and attached once it is
        saved. Its acknowledgement names the photo (``media_burst_of``), so an
        album of ten is answered once, by its last photo, instead of ten times.
        """
        media_kind = _LISTING_MEDIA.get(kind)
        if media_kind is None:
            return Reading(
                intent="UNKNOWN",
                confidence=1.0,
                reason="media_unsupported",
                reply_text=unsupported_media_text(kind.value, session.language),
                media_marker=media_marker(kind),
            )
        property_id = session.assigned_property_id
        reason: str | None = None
        reply_text: str | None = None
        settles_on: FlowStep | None = None
        if self._listing is None or property_id is None:
            if session.flow is FlowName.PROPERTY_SEARCH or self._listing is None:
                reason = "media_without_a_listing"
                _logger.warning(
                    "media_without_a_listing",
                    session_id=str(session.id),
                    object_key=object_key,
                    kind=media_kind,
                )
            else:
                reason = "media_kept_for_listing"
                reply_text = await self._keep(
                    session, object_key=object_key, kind=media_kind, now=now
                )
        else:
            try:
                attached = await self._listing.attach_media_from_conversation(
                    property_id=property_id, object_key=object_key, kind=media_kind, now=now
                )
                if attached is not None and attached.published:
                    # The listing is online: the conversation is too. Left on
                    # COLLECT_MEDIA, it kept asking for the photos it had.
                    settles_on = FlowStep.PUBLISHED
                if attached is not None:
                    reply_text = media_received_text(
                        kind=media_kind,
                        photos=attached.photos,
                        published=attached.published,
                        just_published=attached.just_published,
                        language=session.language,
                    )
            except DomainError as exc:
                # The listing refused it — already attached, or past 30 photos.
                # The turn still happened, and the reason is what tells the
                # landlord why his photo did not count.
                reason = "media_refused"
                _logger.warning(
                    "media_refused",
                    session_id=str(session.id),
                    object_key=object_key,
                    error=exc.message,
                )
        return Reading(
            intent="COLLECT_MEDIA" if reason is None else "UNKNOWN",
            confidence=1.0,
            reason=reason,
            reply_text=reply_text,
            media_burst_of=message_id,
            media_marker=media_marker(kind),
            settles_on=settles_on,
        )

    async def _keep(
        self, session: ConversationSession, *, object_key: str, kind: str, now: datetime
    ) -> str:
        """Hold a photo sent before its listing exists, and say so."""
        observed = session.revision
        earlier = session.context.get(PENDING_MEDIA_KEY)
        kept = [
            *(earlier if isinstance(earlier, list) else []),
            {"key": object_key, "kind": kind},
        ]
        session.merge_context({PENDING_MEDIA_KEY: kept}, now=now)
        await self._writer.save(session, observed=observed)
        return media_kept_text(
            count=len(kept),
            photos_only=all(item.get("kind") == "PHOTO" for item in kept),
            listing_started=session.flow in LISTING_FLOWS,
            language=session.language,
        )

    async def attach_kept(
        self, session: ConversationSession, property_id: uuid.UUID, *, now: datetime
    ) -> ConversationMediaResult | None:
        """The photos sent early, onto the listing just saved."""
        kept = session.context.get(PENDING_MEDIA_KEY)
        if self._listing is None or not isinstance(kept, list):
            return None
        result: ConversationMediaResult | None = None
        for item in kept:
            if not isinstance(item, dict) or not isinstance(item.get("key"), str):
                continue
            try:
                result = await self._listing.attach_media_from_conversation(
                    property_id=property_id,
                    object_key=item["key"],
                    kind=str(item.get("kind") or "PHOTO"),
                    now=now,
                ) or result
            except DomainError as exc:
                _logger.warning(
                    "kept_media_refused", session_id=str(session.id), error=exc.message
                )
        return result
