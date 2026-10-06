"""Fraud handlers on the two queues the context owns.

``payments.events`` reacts to payment outcomes: a failed payment is evidence
of rejection (scored as ``MULTIPLE_REJECTIONS`` when repeated), and a
succeeded payment re-evaluates the same subjects so a stale failure signal
quietens down. Both tenant and landlord are scored atomically, in one
transaction, because the evidence belongs to the same application.

``fraud.analysis`` is the explicit re-evaluation request queue. It exists today
so the analyst console and future producers (e.g. conversation analysis finding
inconsistent details) share one code path.

A message with unparseable ids or an unknown payment is a permanent failure:
retrying it would fail identically, so it goes to the DLQ.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime
from typing import Any

from loka.bounded_contexts.fraud.application.risk_evaluator import RiskEvaluator
from loka.bounded_contexts.fraud.application.use_cases.evaluate_risk import (
    EvaluateRiskCommand,
)
from loka.bounded_contexts.fraud.domain.entities.risk_profile import RiskSubject
from loka.shared.infrastructure.broker.consumer import ConsumerBase, PermanentHandlerError
from loka.shared.infrastructure.broker.topology import Broker, QueueSpec
from loka.shared.infrastructure.composition import (
    evaluate_risk_use_case,
    unit_of_work,
)
from loka.shared.infrastructure.config.settings import Settings
from loka.shared.infrastructure.db.engine import Database
from loka.shared.infrastructure.logging import get_logger

PAYMENTS_EVENTS_QUEUE = "payments.events"
FRAUD_ANALYSIS_QUEUE = "fraud.analysis"

_LIVE_PAYMENT_EVENTS = ("PaymentSucceeded", "PaymentFailed")

_logger = get_logger(__name__)


class PaymentEventsConsumer(ConsumerBase):
    def __init__(
        self, spec: QueueSpec, broker: Broker, database: Database, settings: Settings
    ) -> None:
        super().__init__(broker)
        self.queue_spec = spec
        self._database = database
        self._settings = settings

    async def handle(self, payload: dict[str, Any]) -> None:
        event_type = str(payload.get("event_type") or "")
        if event_type not in _LIVE_PAYMENT_EVENTS:
            _logger.info("fraud_ignored_payment_event", event_type=event_type)
            return
        payment_id = parse_payment_id(payload)
        now = datetime.now(UTC)
        async with unit_of_work(self._database) as uow:
            facts = uow.repository("fraud_facts")
            subjects = await facts.payment_subjects(payment_id)
            if subjects is None:
                raise PermanentHandlerError(f"payment not found: {payment_id}")
            tenant_id, landlord_id = subjects
            evaluator = RiskEvaluator(uow)
            profiles = [
                await evaluator.apply(RiskSubject.USER, tenant_id, now=now),
                await evaluator.apply(RiskSubject.LANDLORD, landlord_id, now=now),
            ]
            for profile in profiles:
                uow.collect(profile)
            await uow.commit()
        _logger.info(
            "fraud_payment_processed",
            event_type=event_type,
            payment_id=str(payment_id),
            tenant_id=str(tenant_id),
            landlord_id=str(landlord_id),
            tenant_score=profiles[0].current_score.score,
            landlord_score=profiles[1].current_score.score,
        )


class FraudAnalysisConsumer(ConsumerBase):
    def __init__(
        self, spec: QueueSpec, broker: Broker, database: Database, settings: Settings
    ) -> None:
        super().__init__(broker)
        self.queue_spec = spec
        self._database = database
        self._settings = settings

    async def handle(self, payload: dict[str, Any]) -> None:
        subject, subject_id = parse_analysis_payload(payload)
        async with unit_of_work(self._database) as uow:
            view = await evaluate_risk_use_case(uow).execute(
                EvaluateRiskCommand(subject=subject, subject_id=subject_id),
                now=datetime.now(UTC),
            )
        _logger.info(
            "fraud_analysis_processed",
            subject=view.subject,
            subject_id=str(view.subject_id),
            score=view.score,
            band=view.band,
        )


def parse_payment_id(payload: dict[str, Any]) -> uuid.UUID:
    """The payment id lives in the event metadata, never on the envelope."""
    body = payload.get("payload")
    if not isinstance(body, dict):
        raise PermanentHandlerError("payment payload is missing or not an object")
    metadata = body.get("metadata")
    if not isinstance(metadata, dict):
        raise PermanentHandlerError("payment payload has no metadata")
    value = metadata.get("payment_id")
    if not isinstance(value, str) or not value.strip():
        raise PermanentHandlerError("payment field 'payment_id' is missing")
    try:
        return uuid.UUID(value)
    except ValueError as exc:
        raise PermanentHandlerError(
            f"payment field 'payment_id' is not a uuid: {value}"
        ) from exc


def parse_analysis_payload(payload: dict[str, Any]) -> tuple[RiskSubject, uuid.UUID]:
    """Extract the subject and its id, or refuse the message permanently."""
    body = payload.get("payload")
    if not isinstance(body, dict):
        raise PermanentHandlerError("fraud analysis payload is missing or not an object")
    metadata = body.get("metadata")
    if not isinstance(metadata, dict):
        raise PermanentHandlerError("fraud analysis payload has no metadata")
    raw_subject = metadata.get("subject")
    raw_subject_id = metadata.get("subject_id")
    if not isinstance(raw_subject, str) or raw_subject not in {
        subject.value for subject in RiskSubject if subject is not RiskSubject.CONVERSATION
    }:
        raise PermanentHandlerError(
            f"fraud analysis field 'subject' is missing or unknown: {raw_subject!r}"
        )
    if not isinstance(raw_subject_id, str) or not raw_subject_id.strip():
        raise PermanentHandlerError("fraud analysis field 'subject_id' is missing")
    try:
        subject_id = uuid.UUID(raw_subject_id)
    except ValueError as exc:
        raise PermanentHandlerError(
            f"fraud analysis field 'subject_id' is not a uuid: {raw_subject_id}"
        ) from exc
    return RiskSubject(raw_subject), subject_id