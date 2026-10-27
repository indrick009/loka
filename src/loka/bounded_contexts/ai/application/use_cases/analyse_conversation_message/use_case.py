"""Understand one inbound message, with the conversation as context.

The pipeline is deliberately boring:

1. Read the message and its session, then **commit** so no transaction is held
   while a network call runs.
2. Ask the model first, handing it the state of the conversation — the flow, the
   step, the question the user is answering and the facts already known — so a
   bare "Bastos" is understood as an answer and not as noise.
3. Keep a deterministic fast lane for the replies where a model adds nothing: an
   exact "oui"/"non" and a message whose only content is a price.
4. Validate whatever came back through the domain value objects, then write the
   turn, the usage and the session state in one transaction.

The model never decides what happens next: it proposes facts, the code decides
whether they are good enough to move the conversation forward. What a turn
*does* belongs to its domain (``housing/``); this file only runs the turn.
"""

from __future__ import annotations

import uuid
from dataclasses import replace
from datetime import datetime
from decimal import Decimal
from typing import Any

from loka.bounded_contexts.ai.application.ports import (
    AiUsageLedger,
    AvailabilityAnswers,
    AvailabilityRequests,
    BudgetPolicy,
    ConversationExchanges,
    ConversationTurnRepository,
    DemandFollowUp,
    Exchange,
    IntentModel,
    PropertyListing,
    PropertySearchGateway,
    ResponseModel,
    TenantDemands,
    TurnSummary,
    UnlockPayments,
)
from loka.bounded_contexts.ai.application.services.decision_shadow import DecisionShadow
from loka.bounded_contexts.ai.application.services.deterministic_classifier import (
    DeterministicClassifier,
)
from loka.bounded_contexts.ai.application.services.prompts import (
    build_analysis_prompt,
    collapse_repeats,
)
from loka.bounded_contexts.ai.application.use_cases.analyse_conversation_message.flow_rules import (
    known_facts,
    target_flow,
)
from loka.bounded_contexts.ai.application.use_cases.analyse_conversation_message.housing.location import (  # noqa: E501
    shared_location,
)
from loka.bounded_contexts.ai.application.use_cases.analyse_conversation_message.housing.media import (  # noqa: E501
    refused_media,
)
from loka.bounded_contexts.ai.application.use_cases.analyse_conversation_message.housing.turns import (  # noqa: E501
    HousingTurns,
)
from loka.bounded_contexts.ai.application.use_cases.analyse_conversation_message.reply_fit import (
    inline_reply_fits,
)
from loka.bounded_contexts.ai.application.use_cases.analyse_conversation_message.reply_writer import (  # noqa: E501
    ReplyWriter,
)
from loka.bounded_contexts.ai.application.use_cases.analyse_conversation_message.session_writer import (  # noqa: E501
    SessionWriter,
)
from loka.bounded_contexts.ai.application.use_cases.analyse_conversation_message.turn import (
    AnalysisCommand,
    AnalysisReport,
    Reading,
    TurnCost,
    Verdict,
)
from loka.bounded_contexts.ai.application.use_cases.analyse_conversation_message.turn_ledger import (  # noqa: E501
    CallUsage,
    TurnLedger,
)
from loka.bounded_contexts.ai.domain.services.ai_provider import SUPPORTED_LANGUAGES
from loka.bounded_contexts.ai.domain.services.fact_extraction import (
    MIN_ACCEPTED_CONFIDENCE,
    FactExtractor,
    is_supported_intent,
)
from loka.bounded_contexts.messaging.domain.entities.conversation_session import (
    ConversationSession,
)
from loka.bounded_contexts.messaging.domain.repositories.conversation_session_repository import (
    ConversationSessionRepository,
)
from loka.bounded_contexts.messaging.domain.repositories.inbound_message_repository import (
    InboundMessageRepository,
    StoredInboundMessage,
)
from loka.shared.application.unit_of_work import UnitOfWork
from loka.shared.domain.clock import ensure_utc
from loka.shared.domain.errors import ExternalServiceUnavailable
from loka.shared.infrastructure.logging import get_logger
from loka.shared.infrastructure.metrics import ai_bypass_total

_logger = get_logger(__name__)


class AnalyseConversationMessageUseCase:
    def __init__(
        self,
        uow: UnitOfWork,
        *,
        inbound: InboundMessageRepository,
        sessions: ConversationSessionRepository,
        turns: ConversationTurnRepository,
        usage: AiUsageLedger,
        classifier: DeterministicClassifier | None = None,
        model: IntentModel | None = None,
        response_model: ResponseModel | None = None,
        extractor: FactExtractor | None = None,
        budget: BudgetPolicy | None = None,
        provider_name: str = "openrouter",
        listing: PropertyListing | None = None,
        availability: AvailabilityAnswers | None = None,
        availability_requests: AvailabilityRequests | None = None,
        unlock_payments: UnlockPayments | None = None,
        demands: TenantDemands | None = None,
        follow_up: DemandFollowUp | None = None,
        search: PropertySearchGateway | None = None,
        shadow: DecisionShadow | None = None,
        exchanges: ConversationExchanges | None = None,
    ) -> None:
        self._uow = uow
        self._exchanges = exchanges
        self._inbound = inbound
        self._sessions = sessions
        self._turns = turns
        self._classifier = classifier or DeterministicClassifier()
        self._model = model
        self._extractor = extractor or FactExtractor()
        self._search = search
        self._shadow = shadow
        self._ledger = TurnLedger(turns, usage, provider=provider_name, budget=budget)
        self._replies = ReplyWriter(response_model)
        self._writer = SessionWriter(uow, sessions)
        self._housing = HousingTurns(
            self._writer,
            classifier=self._classifier,
            listing=listing,
            availability=availability,
            availability_requests=availability_requests,
            unlock_payments=unlock_payments,
            demands=demands,
            follow_up=follow_up,
        )

    async def execute(self, command: AnalysisCommand, *, now: datetime) -> AnalysisReport:
        moment = ensure_utc(now)

        message = await self._inbound.get_by_id(command.message_id)
        session = (
            await self._sessions.get_by_id(command.session_id) if message is not None else None
        )
        if message is None or session is None:
            # Nothing was written yet, and inventing a session would be worse
            # than admitting the message is gone.
            return AnalysisReport(
                session_id=command.session_id,
                message_id=command.message_id,
                intent="UNKNOWN",
                confidence=0.0,
                used_llm=False,
                accepted=False,
                reason="message_not_found" if message is None else "session_not_found",
            )

        history = await self._turns.recent(session.id, limit=5)
        # The words, not only the labels: what the user and Kora actually said.
        thread = (
            await self._exchanges.recent(session.id, excluding=command.message_id)
            if self._exchanges is not None
            else []
        )

        # Nothing has been written yet, so releasing the transaction costs
        # nothing and the model call never holds a database connection.
        await self._uow.commit()

        # Asked before the turn and read after it: the decision model works
        # while the pipeline does, and the user waits for neither. A pin or a
        # photo carries no sentence to decide on.
        shadow = (
            self._shadow.start(session, message_id=command.message_id, text=message.text or "")
            if self._shadow is not None
            and message.location is None
            and message.media_object_key is None
            else None
        )
        try:
            report = await self._analyse(command, message, session, history, thread, moment)
        except BaseException:
            if shadow is not None:
                DecisionShadow.cancel(shadow)
            raise
        if shadow is not None and self._shadow is not None:
            await self._shadow.finish(
                shadow,
                intent=report.intent,
                accepted=report.accepted,
                used_llm=report.used_llm,
                next_step=report.next_step,
                now=moment,
            )
        return report

    async def _analyse(
        self,
        command: AnalysisCommand,
        message: StoredInboundMessage,
        session: ConversationSession,
        history: list[TurnSummary],
        thread: list[Exchange],
        moment: datetime,
    ) -> AnalysisReport:
        """Read one loaded message and answer it."""
        text = message.text or ""
        pending = session.context.get("next_question")
        pending_key = pending if isinstance(pending, str) else None
        message_id = command.message_id

        # A pin and a photo are things the user *sent*, not sentences needing
        # interpretation. They are read here, before any model: paying one to
        # look at two floats would be slower, dearer and less reliable than
        # reading the fields.
        if message.location is not None:
            ai_bypass_total.labels(stage="location").inc()
            return await self._finish(
                session, message_id, shared_location(message.location), now=moment
            )
        if message.media_refused is not None:
            ai_bypass_total.labels(stage="media").inc()
            reading = refused_media(session, kind=message.kind, refused=message.media_refused)
            return await self._finish(session, message_id, reading, now=moment, text=text)
        if message.media_object_key is not None:
            ai_bypass_total.labels(stage="media").inc()
            reading = await self._housing.media.read(
                session,
                message_id=message_id,
                object_key=message.media_object_key,
                kind=message.kind,
                now=moment,
            )
            return await self._finish(session, message_id, reading, now=moment, text=text)

        if self._model is None:
            return await self._without_a_model(
                session, message_id=message_id, text=text, pending=pending_key, now=moment
            )

        # The rules are a fast lane now, not the primary reader: only replies a
        # model could not improve ("oui", "250 000 fcfa") skip the call. The open
        # question is passed along so a bare "non" answers "moderne ou non"
        # instead of being read as a refusal.
        shortcut = self._classifier.shortcut(text, pending=pending_key)
        if shortcut is not None:
            ai_bypass_total.labels(stage="regex").inc()
            intent, entities = self._housing.read_open_question(
                pending_key=pending_key,
                hint=shortcut,
                intent=shortcut.intent,
                entities=dict(shortcut.entities),
                text=text,
            )
            reading = Reading(intent=intent, confidence=shortcut.confidence, entities=entities)
            return await self._finish(session, message_id, reading, now=moment, text=text)

        decision = await self._ledger.budget(session, now=moment)
        if not decision.allowed:
            ai_bypass_total.labels(stage="budget").inc()
            reading = Reading(intent="UNKNOWN", confidence=0.0, reason=decision.reason)
            return await self._finish(session, message_id, reading, now=moment, text=text)

        return await self._with_the_model(
            session,
            message=message,
            message_id=message_id,
            pending_key=pending_key,
            history=history,
            thread=thread,
            now=moment,
        )

    async def _with_the_model(
        self,
        session: ConversationSession,
        *,
        message: StoredInboundMessage,
        message_id: uuid.UUID,
        pending_key: str | None,
        history: list[TurnSummary],
        thread: list[Exchange],
        now: datetime,
    ) -> AnalysisReport:
        assert self._model is not None
        text = message.text or ""
        ai_bypass_total.labels(stage="llm").inc()
        # A cheap rule reading only picks which form the prompt shows; the model
        # still chooses the intent. On an opening turn it is what tells a tenant
        # the search fields instead of the landlord's.
        hint = self._classifier.classify(text, pending=pending_key)
        prompt_flow = target_flow(session, hint.intent) if hint is not None else session.flow
        prompt = build_analysis_prompt(
            text=message.text,
            default_language=session.language,
            history=history,
            flow=prompt_flow,
            step=session.step,
            pending_question=pending_key,
            known_facts=dict(session.context),
            exchanges=thread,
        )
        try:
            result = await self._model.understand_intent(prompt)
        except ExternalServiceUnavailable as exc:
            # The provider is down, timing out, or answering with something
            # unusable. Letting this escape meant the broker retried the
            # message and then parked it in the DLQ, and the user's message was
            # never answered at all — the silence this pipeline exists to
            # prevent. A worse reply beats no reply, and the turn is recorded
            # so the outage stays measurable.
            _logger.warning(
                "analysis_degraded_to_rules",
                session_id=str(session.id),
                message_id=str(message_id),
                error=exc.message,
            )
            ai_bypass_total.labels(stage="provider_down").inc()
            return await self._without_a_model(
                session, message_id=message_id, text=text, pending=pending_key, now=now
            )
        # The model read the message, so it is the one that knows which language
        # the user is writing in. Adopting its answer keeps a bare "150000" in
        # tomorrow's fallback in the right language instead of snapping to French.
        if result.language in SUPPORTED_LANGUAGES:
            session.language = result.language
        entities = dict(result.entities)
        # The locale shorthand ("50 mil") is a rule, not a guess: when the
        # message spells one it overrides whatever number the model proposed,
        # because the model's prior reads "mil" as a million.
        shorthand = self._classifier.shorthand_amount(text)
        if shorthand is not None:
            entities["price"] = shorthand
        intent, entities = self._housing.read_open_question(
            pending_key=pending_key, hint=hint, intent=result.intent, entities=entities, text=text
        )
        verdict = self._verdict(
            session, intent=intent, confidence=result.confidence, entities=entities, reason=None
        )

        # Preferred path: the classification call already wrote the reply in its
        # JSON, so the whole turn costs one round-trip. Only a reply that does
        # not ask what was decided pays for a second call, written before the
        # write transaction opens.
        reply_text: str | None = result.reply
        if not inline_reply_fits(
            prompt_flow, session=session, verdict=verdict, asking=result.asking
        ):
            reply_text = None
        reply_usage = None
        if reply_text is None:
            reply_text, reply_usage = await self._replies.compose(
                verdict=verdict, text=text, session=session, history=history, thread=thread
            )

        call = CallUsage.of(result.usage, reply_usage, model=self._model.model)
        call.observe()
        cost = TurnCost(
            message_id=message_id,
            confidence=result.confidence,
            used_llm=True,
            latency_ms=call.latency_ms,
            cost_usd=call.cost_usd,
            now=now,
        )
        return await self._apply(
            session, verdict, cost, call=call, reply_text=reply_text, text=text
        )

    async def _without_a_model(
        self,
        session: ConversationSession,
        *,
        message_id: uuid.UUID,
        text: str,
        pending: str | None,
        now: datetime,
    ) -> AnalysisReport:
        """No model to ask: fall back to the full rule set and the templates.

        An unresolved message still records its turn and asks the user to
        rephrase instead of leaving the thread silent.
        """
        match = self._classifier.classify(text, pending=pending)
        if match is not None:
            ai_bypass_total.labels(stage="regex").inc()
            reading = Reading(
                intent=match.intent, confidence=match.confidence, entities=dict(match.entities)
            )
            return await self._finish(session, message_id, reading, now=now, text=text)
        ai_bypass_total.labels(stage="budget").inc()
        reading = Reading(intent="UNKNOWN", confidence=0.0, reason="no_model_configured")
        return await self._finish(session, message_id, reading, now=now, text=text)

    async def _finish(
        self,
        session: ConversationSession,
        message_id: uuid.UUID,
        reading: Reading,
        *,
        now: datetime,
        text: str | None = None,
    ) -> AnalysisReport:
        """Close a turn that did not reach the model.

        ``text`` is the raw message, handed on so a step read by a rule alone —
        the 1 000 FCFA answer — can be decided without a model.
        """
        verdict = self._verdict(
            session,
            intent=reading.intent,
            confidence=reading.confidence,
            entities=reading.entities,
            reason=reading.reason,
        )
        if reading.settles_on is not None and verdict.accepted:
            verdict = replace(verdict, next_step=reading.settles_on)
        cost = TurnCost(
            message_id=message_id,
            confidence=reading.confidence,
            used_llm=False,
            latency_ms=0,
            cost_usd=Decimal("0"),
            now=now,
        )
        return await self._apply(
            session,
            verdict,
            cost,
            text=text,
            reply_text=reading.reply_text,
            media_burst_of=reading.media_burst_of,
            media_marker=reading.media_marker,
        )

    def _verdict(
        self,
        session: ConversationSession,
        *,
        intent: str,
        confidence: float,
        entities: dict[str, Any],
        reason: str | None,
    ) -> Verdict:
        """Validate a proposal through the domain, before anything is written.

        Kept apart from ``_apply`` so the reply can be written from the
        *outcome* of the turn while the write transaction is still closed.
        """
        if not is_supported_intent(intent):
            intent, reason = "UNKNOWN", reason or "unsupported_intent"
        if reason is None and confidence < MIN_ACCEPTED_CONFIDENCE:
            reason = "low_confidence"
        facts = self._extractor.validate(self._housing.one_place(entities, session))
        # The next step must consider the whole form, not just this message: a
        # landlord who gives their city after naming the property type is one
        # step further, not back at the type question. When this message opens a
        # *new* flow, the old facts belong to the abandoned attempt and are
        # deliberately ignored — matching the reset the session writer does.
        next_step = (
            self._extractor.next_step(
                intent,
                facts,
                flow=target_flow(session, intent),
                known=known_facts(session, intent),
            )
            if reason is None
            else None
        )
        return Verdict(
            intent=intent,
            facts=facts,
            accepted=reason is None,
            reason=reason,
            next_step=next_step,
        )

    async def _apply(
        self,
        session: ConversationSession,
        verdict: Verdict,
        cost: TurnCost,
        *,
        call: CallUsage | None = None,
        reply_text: str | None = None,
        text: str | None = None,
        media_burst_of: uuid.UUID | None = None,
        media_marker: str | None = None,
    ) -> AnalysisReport:
        """Record the turn, move the conversation, and stage the answer.

        A rejected outcome is still recorded, so "the model was unsure" stays
        measurable instead of being silently converted into a wrong listing.
        """
        await self._ledger.record(session, intent=verdict.intent, cost=cost, call=call)
        # The model's sentence is swept for decoding loops here: the platform
        # copy that may replace it needs no such handling.
        reply_text = collapse_repeats(reply_text) if reply_text else reply_text

        settled = await self._housing.settle(
            session, text=text, intent=verdict.intent, now=cost.now
        )
        if settled is not None:
            return await self._writer.close(session, settled, cost)

        outcome = (
            await self._housing.apply(
                session, verdict, text=text, reply_text=reply_text, now=cost.now
            )
            if verdict.accepted
            else self._housing.refused(session, intent=verdict.intent, reply_text=reply_text)
        )
        next_step = outcome.next_step if verdict.accepted else verdict.next_step

        # Committed with the turn: an analysis the platform understood but never
        # answered is a landlord staring at a silent thread, and no retry can
        # notice a reply that was never staged.
        self._writer.stage_reply(
            session,
            intent=verdict.intent,
            reason=verdict.reason,
            question_key=outcome.question,
            text=outcome.reply_text,
            now=cost.now,
            # Stock copy goes out in the user's voice; a recap to confirm and
            # the model's own sentence go out as they are. A photo's caption
            # may be empty, and its acknowledgement is stock copy too, as is
            # the fast lane's template ("1", "60mil", "6").
            answering=(
                (text or media_marker)
                if media_marker is not None
                else text
                if (outcome.platform_text and outcome.reply_text == outcome.platform_text)
                or outcome.reply_text is None
                else None
            ),
            media_burst_of=media_burst_of,
        )
        await self._uow.commit()
        return AnalysisReport(
            session_id=session.id,
            message_id=cost.message_id,
            intent=verdict.intent,
            confidence=cost.confidence,
            used_llm=cost.used_llm,
            accepted=verdict.accepted,
            reason=verdict.reason,
            facts=verdict.facts.as_context() if verdict.accepted else {},
            rejected_facts=dict(verdict.facts.rejected),
            next_step=next_step.value if next_step else None,
            question_key=outcome.question,
            cost_usd=cost.cost_usd,
        )
