"""Outbound WhatsApp delivery.

Drains ``whatsapp.outgoing`` and hands the sentence to the gateway. The
conversation layer decides *what* to say and the gateway decides *how* it
reaches the phone; this consumer is the seam between them, and the only place
that knows both.

Two rules the rest of the system relies on:

* **Nothing is sent before the copy is rendered.** An untranslated
  ``question_key`` reaching a landlord is a variable name in a conversation,
  so an unknown key degrades to the clarification instead of being dropped.
* **A send that may still succeed is retried, a send that can never succeed
  is not.** A gateway 4xx means the destination or the body is wrong: retrying
  it produces the same answer five times before parking the message, so it
  goes to the DLQ on the first attempt.
"""

from __future__ import annotations

import uuid
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from datetime import UTC, datetime, timedelta
from typing import Any

from redis.asyncio import Redis

from loka.bounded_contexts.conversation.application.ports import Exchange
from loka.bounded_contexts.conversation.domain.value_objects.rich_reply import RichReply
from loka.bounded_contexts.conversation.infrastructure.adapters.redis_holds import (
    RedisConversationHolds,
)
from loka.bounded_contexts.conversation.infrastructure.composition import (
    voice_platform_reply_use_case,
)
from loka.bounded_contexts.conversation.infrastructure.persistence.media_bursts import (
    SqlAlchemyMediaBursts,
)
from loka.bounded_contexts.conversation.infrastructure.persistence.sent_messages import (
    SqlAlchemySentMessageLog,
)
from loka.interfaces.worker.conversation_analysis_consumer import (
    ConversationLockFactory,
    redis_lock_factory,
)
from loka.interfaces.worker.stuck_guard import (
    ESCALATE,
    UNSUPPORTED,
    StuckGuard,
    holding_text,
)
from loka.interfaces.worker.whatsapp_channels import GatewayDown, channel_for
from loka.interfaces.worker.whatsapp_replies import render_reply
from loka.shared.infrastructure.broker.consumer import (
    ConsumerBase,
    DeferredHandlerError,
    PermanentHandlerError,
    RetryableHandlerError,
)
from loka.shared.infrastructure.broker.topology import Broker, QueueSpec
from loka.shared.infrastructure.composition import unit_of_work
from loka.shared.infrastructure.config.settings import Settings
from loka.shared.infrastructure.conversation_log import journal
from loka.shared.infrastructure.db.engine import Database
from loka.shared.infrastructure.logging import get_logger
from loka.shared.infrastructure.metrics import whatsapp_failures_total
from loka.shared.infrastructure.object_storage.media import (
    MediaStorage,
    MediaStoreError,
    S3MediaStorage,
)
from loka.shared.infrastructure.redis_client import (
    LockBackendUnavailable,
    NotAcquired,
    RedisRateLimiter,
)

OUTBOUND_QUEUE = "whatsapp.outgoing"

# While WhatsApp itself is unreachable, a reply waits for it instead of burning
# its retries in fifteen seconds and landing in the dead-letter queue. Observed
# live: a short disconnect lost every message sent during it. Past this age a
# reply is stale, and the ordinary retries take over.
GATEWAY_DOWN_PATIENCE = timedelta(minutes=30)
GATEWAY_DOWN_RECHECK_MS = 15_000
# The messages to one phone go out in the order they were meant for.
_IN_LINE = "outbound:line:{}"
IN_LINE_RECHECK_MS = 700
IN_LINE_STALE_SECONDS = 120
IN_LINE_MAX_WAIT_SECONDS = 10
_IN_LINE_WAIT = "outbound:waiting:{}"

__all__ = ["OUTBOUND_QUEUE", "GatewayDown", "WhatsAppOutboundConsumer"]

_logger = get_logger(__name__)


class WhatsAppOutboundConsumer(ConsumerBase):
    def __init__(
        self,
        spec: QueueSpec,
        broker: Broker,
        database: Database,
        settings: Settings,
        *,
        lock_factory: ConversationLockFactory | None = None,
    ) -> None:
        super().__init__(broker)
        self.queue_spec = spec
        self._database = database
        self._settings = settings
        self._lock_factory = lock_factory or redis_lock_factory(settings)
        whatsapp = settings.whatsapp
        self._limit_per_minute = whatsapp.max_outbound_per_minute
        # Presigning lives here, not in the analysis: a URL that expires has to
        # be minted moments before the send, and the domain only holds the key.
        self._media: MediaStorage = S3MediaStorage(settings.object_storage)
        self._media_ttl = settings.object_storage.presigned_url_ttl_seconds
        # The official API when it is set up, Baileys otherwise: the same
        # reply, shown as richly as the channel allows.
        self._channel = channel_for(settings)
        # Built, not awaited: redis-py connects lazily, and a rate limiter whose
        # backend is down must degrade to "allow" rather than stop replies.
        redis = Redis.from_url(settings.redis_url, decode_responses=True)
        self._redis = redis
        self._rate_limiter = RedisRateLimiter(redis)
        self._guard = StuckGuard(
            database,
            RedisConversationHolds(redis),
            enabled=whatsapp.stuck_guard_enabled,
            alert_phones=[p.strip() for p in whatsapp.alert_phones.split(",") if p.strip()],
        )

    async def stop(self) -> None:
        await super().stop()
        await self._channel.aclose()

    async def handle(self, payload: dict[str, Any]) -> None:
        body = payload.get("payload")
        if not isinstance(body, dict):
            raise PermanentHandlerError("outbound payload is missing or not an object")
        metadata = body.get("metadata")
        if not isinstance(metadata, dict):
            raise PermanentHandlerError("outbound payload has no metadata")

        recipient = _require_str(metadata, "recipient_phone")
        not_before = _optional_datetime(metadata.get("not_before"))
        if not_before is not None:
            wait_ms = int((not_before - datetime.now(UTC)).total_seconds() * 1000)
            if wait_ms > 0:
                raise DeferredHandlerError(wait_ms)
        burst_of = _optional_uuid(metadata.get("media_burst_of"))
        if burst_of is not None and await self._a_newer_photo_follows(burst_of):
            _logger.info("photo_ack_superseded", session_id=metadata.get("session_id"))
            return
        place = str(body.get("event_id") or uuid.uuid4())
        rank = (not_before or _optional_datetime(body.get("occurred_at")) or datetime.now(UTC))
        try:
            async with self._in_thread_order(recipient):
                if not await self._first_in_line(recipient, place, rank.timestamp()):
                    raise DeferredHandlerError(IN_LINE_RECHECK_MS)
                try:
                    await self._deliver(body, metadata, recipient)
                except PermanentHandlerError:
                    await self._leave_line(recipient, place)
                    raise
                await self._leave_line(recipient, place)
        except GatewayDown:
            sent_at = _optional_datetime(body.get("occurred_at"))
            if sent_at is not None and datetime.now(UTC) - sent_at < GATEWAY_DOWN_PATIENCE:
                raise DeferredHandlerError(GATEWAY_DOWN_RECHECK_MS) from None
            raise

    async def _a_newer_photo_follows(self, message_id: uuid.UUID) -> bool:
        """Whether this acknowledgement is not the last of its burst.

        A failed check answers "no": an extra "photo reçue" is a small cost,
        a burst left with no answer at all is the silence idia must never give.
        """
        try:
            async with unit_of_work(self._database) as uow:
                return await SqlAlchemyMediaBursts(uow.session).has_newer_media(message_id)
        except Exception as exc:  # an unanswered burst is worse than two answers
            _logger.warning("photo_burst_check_failed", error=type(exc).__name__)
            return False

    async def _first_in_line(self, recipient: str, place: str, rank: float) -> bool:
        """Whether this message may go now, keeping the order it was meant in.

        The lock alone does not order: photos staged together all reached it
        at once and took it in any order — 2, 3, 1, 4, 5 under a list numbered
        1 to 5 (journal of 16 Nov 2026, 09:20). Each message takes its place
        by the time it is due, and waits for those before it — but never more
        than ``IN_LINE_MAX_WAIT_SECONDS``: a message ahead that died, or a
        gateway down, must not stop the thread (20 Nov 2026, 17:52: the
        WhatsApp session was logged out and the line held every reply after
        it). Places are dropped by the clock, not by the ranks waiting.
        Redis down: unordered, never silent.
        """
        key = _IN_LINE.format(recipient)
        waited = _IN_LINE_WAIT.format(place)
        now = datetime.now(UTC).timestamp()
        try:
            await self._redis.zadd(key, {place: rank}, nx=True)
            await self._redis.zremrangebyscore(key, 0, now - IN_LINE_STALE_SECONDS)
            await self._redis.expire(key, 600)
            await self._redis.set(waited, now, nx=True, ex=600)
            since = float(await self._redis.get(waited) or now)
            first = await self._redis.zrange(key, 0, 0)
        except Exception as exc:  # see the docstring
            _logger.warning("outbound_line_unavailable", error=type(exc).__name__)
            return True
        if now - since > IN_LINE_MAX_WAIT_SECONDS:
            _logger.info("outbound_line_overtaken", recipient=recipient)
            return True
        return not first or first[0] == place

    async def _leave_line(self, recipient: str, place: str) -> None:
        try:
            await self._redis.zrem(_IN_LINE.format(recipient), place)
            await self._redis.delete(_IN_LINE_WAIT.format(place))
        except Exception as exc:  # the stale cut-off frees the line anyway
            _logger.warning("outbound_line_unavailable", error=type(exc).__name__)

    @asynccontextmanager
    async def _in_thread_order(self, recipient: str) -> AsyncIterator[None]:
        """One send at a time per phone, so a thread reads in the order written.

        Observed live: the acknowledgement of a search, slowed by its rewrite,
        reached the tenant *after* the lodging found for it — "bonne nouvelle"
        followed by "si un logement correspond, je reviens vers toi". Photos
        captioned 1/3, 2/3, 3/3 race the same way. Ordering is a courtesy,
        delivery is not: a lock backend that is down sends unordered.
        """
        try:
            lock = self._lock_factory(f"outbound:{recipient}")
            await lock.__aenter__()
        except LockBackendUnavailable as exc:
            _logger.warning("outbound_order_unavailable", error=str(exc))
            yield
            return
        except NotAcquired as exc:
            raise RetryableHandlerError(f"a send to {recipient} is still in flight") from exc
        try:
            yield
        finally:
            await lock.__aexit__(None, None, None)

    async def _deliver(
        self, body: dict[str, Any], metadata: dict[str, Any], recipient: str
    ) -> None:
        reply_kind = str(metadata.get("reply_kind") or "ACK")
        session_id = _optional_uuid(metadata.get("session_id"))
        if metadata.get("intent") == ESCALATE and session_id is not None:
            # Not an answer: a request idia does not offer yet. A person decides.
            if not await self._guard.held(session_id):
                thread = await self._guard.thread(session_id)
                request = _optional_str(metadata, "text") or ""
                await self._hold(
                    body, metadata, session_id, recipient, request, (UNSUPPORTED, thread)
                )
            return
        # A model-written sentence wins; the template is the fallback for the
        # deterministic path and for every deployment without an AI key.
        text = _optional_str(metadata, "text") or render_reply(
            reply_kind=reply_kind,
            question_key=_optional_str(metadata, "question_key"),
            intent=_optional_str(metadata, "intent"),
            language=_optional_str(metadata, "language"),
        )
        answering = _optional_str(metadata, "answering")
        if answering is not None and session_id is not None:
            text = await self._in_user_voice(
                text,
                answering=answering,
                session_id=session_id,
                user_id=_optional_uuid(body.get("actor_id")),
            )
        if session_id is not None and StuckGuard.watches(metadata):
            if await self._guard.held(session_id):
                # A reply written before the hold was set: the person released
                # the thread answers it, not a sentence from before the fix.
                _logger.info("reply_dropped_conversation_held", session_id=str(session_id))
                return
            stuck = await self._guard.check(text, metadata, session_id)
            if stuck is not None:
                await self._hold(body, metadata, session_id, recipient, text, stuck)
                return
        # A photo, when the message names one, is sent with the sentence as its
        # caption: the tenant reads what was found and sees it in one message.
        image_url = await self._image_url(_optional_str(metadata, "media_object_key"))

        # A conversation may not answer faster than the platform can absorb its
        # own traffic; the counter is best-effort, never a correctness guard.
        allowed = await self._rate_limiter.allow(
            "whatsapp.outbound", limit=self._limit_per_minute, window_seconds=60
        )
        if not allowed:
            raise RetryableHandlerError(
                f"outbound rate limit of {self._limit_per_minute}/min reached"
            )

        rich = RichReply.from_dict(metadata.get("rich"))
        await self._channel.send(to=recipient, text=text, image_url=image_url, rich=rich)
        await self._record_sent(
            event_id=body.get("event_id"), session_id=session_id, recipient=recipient, text=text
        )
        journal(
            "out", recipient, text=text, media=metadata.get("media_object_key"),
            intent=metadata.get("intent"), reply_kind=reply_kind,
            question_key=metadata.get("question_key"),
        )
        _logger.info(
            "outbound_message_sent",
            recipient=recipient,
            reply_kind=reply_kind,
            question_key=metadata.get("question_key"),
            session_id=metadata.get("session_id"),
            characters=len(text),
            media=image_url is not None,
        )

    async def _hold(
        self,
        body: dict[str, Any],
        metadata: dict[str, Any],
        session_id: uuid.UUID,
        recipient: str,
        withheld: str,
        stuck: tuple[str, list[Exchange]],
    ) -> None:
        """Tell the user an answer is coming, hold the thread, alert a person.

        The user first: were the hold set and his message then failed, the
        retry would find a held thread and tell him nothing at all.
        """
        signal, thread = stuck
        language = _optional_str(metadata, "language")
        waiting = holding_text(language, signal)
        await self._channel.send(to=recipient, text=waiting)
        await self._record_sent(
            event_id=body.get("event_id"), session_id=session_id, recipient=recipient, text=waiting
        )
        journal("out", recipient, text=waiting, reply_kind="HOLD")
        alerts = await self._guard.hold(
            session_id=session_id,
            recipient=recipient,
            signal=signal,
            language=language,
            withheld=withheld,
            thread=thread,
        )
        for phone, alert in alerts:
            try:
                await self._channel.send(to=phone, text=alert)
            except Exception as exc:  # the journal and the log still hold the incident
                _logger.warning("stuck_alert_failed", error=type(exc).__name__)

    async def _in_user_voice(
        self,
        text: str,
        *,
        answering: str,
        session_id: uuid.UUID,
        user_id: uuid.UUID | None,
    ) -> str:
        """The platform's sentence in the user's register, or as it is.

        Best effort by design: the rewrite is a better way of saying what is
        already decided, never a condition for saying it.
        """
        try:
            async with unit_of_work(self._database) as uow:
                voice = voice_platform_reply_use_case(uow, settings=self._settings)
                if voice is None:
                    return text
                return await voice.execute(
                    session_id=session_id,
                    user_id=user_id,
                    message=answering,
                    platform_text=text,
                    now=datetime.now(UTC),
                )
        except Exception as exc:  # the stock sentence still goes out
            _logger.warning(
                "outbound_voice_failed", session_id=str(session_id), error=type(exc).__name__
            )
            return text

    async def _record_sent(
        self, *, event_id: Any, session_id: uuid.UUID | None, recipient: str, text: str
    ) -> None:
        """Keep the words the user read, so the next turn can see them.

        After the send, and never able to undo it: a message already on the
        phone must not be retried because its copy could not be stored.
        """
        try:
            async with unit_of_work(self._database) as uow:
                await SqlAlchemySentMessageLog(uow.session).record(
                    idempotency_key=str(event_id or uuid.uuid4()),
                    session_id=session_id,
                    recipient_phone=recipient,
                    text=text,
                    now=datetime.now(UTC),
                )
                await uow.commit()
        except Exception as exc:  # see the docstring
            _logger.warning(
                "outbound_record_failed", session_id=str(session_id), error=type(exc).__name__
            )

    async def _image_url(self, object_key: str | None) -> str | None:
        """A short-lived URL for one stored object, or ``None`` when it fails.

        The photo is an extra on top of the sentence that says what was found:
        an object storage that is down must not cost the tenant that sentence,
        so the presign failure degrades to text rather than failing the send.
        """
        if not object_key:
            return None
        try:
            return await self._media.presigned_download_url(
                object_key, ttl_seconds=self._media_ttl
            )
        except MediaStoreError as exc:
            whatsapp_failures_total.labels(operation="presign", reason="failed").inc()
            _logger.warning(
                "outbound_media_presign_failed",
                object_key=object_key,
                error=str(exc),
            )
            return None


def _require_str(metadata: dict[str, Any], name: str) -> str:
    value = metadata.get(name)
    if not isinstance(value, str) or not value.strip():
        raise PermanentHandlerError(f"outbound field '{name}' is missing")
    return value


def _optional_str(metadata: dict[str, Any], name: str) -> str | None:
    value = metadata.get(name)
    return value if isinstance(value, str) and value.strip() else None


def _optional_uuid(value: Any) -> uuid.UUID | None:
    try:
        return uuid.UUID(str(value)) if value else None
    except ValueError:
        return None


def _optional_datetime(value: Any) -> datetime | None:
    try:
        return datetime.fromisoformat(str(value)) if value else None
    except ValueError:
        return None
