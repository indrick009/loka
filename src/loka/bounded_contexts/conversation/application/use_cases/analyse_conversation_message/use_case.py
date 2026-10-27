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

from loka.bounded_contexts.conversation.application.ports import (
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
from loka.bounded_contexts.conversation.application.services.decision_shadow import DecisionShadow
from loka.bounded_contexts.conversation.application.use_cases.analyse_conversation_message.model_lane import (  # noqa: E501
    ModelLane,
)
from loka.bounded_contexts.conversation.application.use_cases.analyse_conversation_message.reply_writer import (  # noqa: E501
    ReplyWriter,
)
from loka.bounded_contexts.conversation.application.use_cases.analyse_conversation_message.session_writer import (  # noqa: E501
    SessionWriter,
)
from loka.bounded_contexts.conversation.application.use_cases.analyse_conversation_message.turn import (  # noqa: E501
    AnalysisCommand,
    AnalysisReport,
    Reading,
    TurnCost,
)
from loka.bounded_contexts.conversation.application.use_cases.analyse_conversation_message.turn_applier import (  # noqa: E501
    TurnApplier,
)
from loka.bounded_contexts.conversation.application.use_cases.analyse_conversation_message.turn_ledger import (  # noqa: E501
    TurnLedger,
)
from loka.bounded_contexts.conversation.application.use_cases.analyse_conversation_message.verdicts import (  # noqa: E501
    Judge,
)
from loka.bounded_contexts.conversation.domain.entities.conversation_session import (
    ConversationSession,
)
from loka.bounded_contexts.conversation.domain.repositories.conversation_session_repository import (
    ConversationSessionRepository,
)
from loka.bounded_contexts.conversation.domain.repositories.inbound_message_repository import (
    InboundMessageRepository,
    StoredInboundMessage,
)
from loka.bounded_contexts.housing.conversation.facts import (
    FactExtractor,
)
from loka.bounded_contexts.housing.conversation.rules import (
    DeterministicClassifier,
)
from loka.bounded_contexts.housing.conversation.turns.location import (
    shared_location,
)
from loka.bounded_contexts.housing.conversation.turns.media import (
    refused_media,
)
from loka.bounded_contexts.housing.conversation.turns.turns import (
    HousingTurns,
)
from loka.shared.application.unit_of_work import UnitOfWork
from loka.shared.domain.clock import ensure_utc
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
        self._search = search
        self._shadow = shadow
        self._ledger = TurnLedger(turns, usage, provider=provider_name, budget=budget)
        self._judge = Judge(extractor or FactExtractor())
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
        self._applier = TurnApplier(
            uow, ledger=self._ledger, writer=self._writer, housing=self._housing
        )
        self._lane = (
            ModelLane(
                model,
                classifier=self._classifier,
                housing=self._housing,
                judge=self._judge,
                replies=ReplyWriter(response_model),
            )
            if model is not None
            else None
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

        if self._lane is None:
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

        turn = await self._lane.read(
            session,
            message=message,
            message_id=message_id,
            pending_key=pending_key,
            history=history,
            thread=thread,
            now=moment,
        )
        if turn is None:
            return await self._without_a_model(
                session, message_id=message_id, text=text, pending=pending_key, now=moment
            )
        return await self._applier.apply(
            session,
            turn.verdict,
            turn.cost,
            call=turn.call,
            reply_text=turn.reply_text,
            text=text,
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
        verdict = self._judge(
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
        return await self._applier.apply(
            session,
            verdict,
            cost,
            text=text,
            reply_text=reading.reply_text,
            media_burst_of=reading.media_burst_of,
            media_marker=reading.media_marker,
        )
