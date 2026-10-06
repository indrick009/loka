"""The fraud consumers' payload contract and the queues they own.

A malformed message is permanent: retrying it would fail identically forever,
so these cases pin down which payloads are refused (visible as DLQ entries) so
a bad producer cannot spin a retry loop that never drains.
"""

from __future__ import annotations

import uuid

import pytest

from loka.bounded_contexts.fraud.domain.entities.risk_profile import RiskSubject
from loka.interfaces.worker.fraud_consumer import (
    FRAUD_ANALYSIS_QUEUE,
    PAYMENTS_EVENTS_QUEUE,
    parse_analysis_payload,
    parse_payment_id,
)
from loka.shared.infrastructure.broker.consumer import PermanentHandlerError

pytestmark = pytest.mark.unit


def analysis_envelope(**metadata: object) -> dict[str, object]:
    base = {"subject": "USER", "subject_id": str(uuid.uuid4())}
    return {
        "event_id": str(uuid.uuid4()),
        "event_type": "FraudRiskEvaluationRequested",
        "payload": {"metadata": {**base, **metadata}},
    }


def payment_envelope(**metadata: object) -> dict[str, object]:
    base = {"payment_id": str(uuid.uuid4())}
    return {
        "event_id": str(uuid.uuid4()),
        "event_type": "PaymentSucceeded",
        "payload": {"metadata": {**base, **metadata}},
    }


class TestParsePaymentId:
    def test_the_id_is_extracted(self) -> None:
        body = payment_envelope()
        metadata = body["payload"]["metadata"]  # type: ignore[index]

        payment_id = parse_payment_id(body)  # type: ignore[arg-type]

        assert str(payment_id) == str(metadata["payment_id"])

    def test_a_missing_payload_is_refused(self) -> None:
        with pytest.raises(PermanentHandlerError, match="payload"):
            parse_payment_id({})

    def test_a_payload_without_metadata_is_refused(self) -> None:
        with pytest.raises(PermanentHandlerError, match="metadata"):
            parse_payment_id({"payload": {}})

    def test_a_missing_payment_id_is_refused(self) -> None:
        body = payment_envelope()
        del body["payload"]["metadata"]["payment_id"]  # type: ignore[index]

        with pytest.raises(PermanentHandlerError, match="payment_id"):
            parse_payment_id(body)  # type: ignore[arg-type]

    def test_a_malformed_uuid_is_refused(self) -> None:
        with pytest.raises(PermanentHandlerError, match="not a uuid"):
            parse_payment_id(payment_envelope(payment_id="nope"))


class TestParseAnalysisPayload:
    def test_the_subject_and_id_are_extracted(self) -> None:
        body = analysis_envelope()
        metadata = body["payload"]["metadata"]  # type: ignore[index]

        subject, subject_id = parse_analysis_payload(body)  # type: ignore[arg-type]

        assert subject is RiskSubject.USER
        assert str(subject_id) == str(metadata["subject_id"])

    def test_a_valid_subject_value_is_accepted(self) -> None:
        for raw in ("USER", "LANDLORD", "PROPERTY"):
            subject, _ = parse_analysis_payload(analysis_envelope(subject=raw))
            assert subject.value == raw

    def test_a_missing_payload_is_refused(self) -> None:
        with pytest.raises(PermanentHandlerError, match="payload is missing"):
            parse_analysis_payload({})

    def test_a_payload_without_metadata_is_refused(self) -> None:
        with pytest.raises(PermanentHandlerError, match="metadata"):
            parse_analysis_payload({"payload": {}})

    def test_an_unknown_subject_is_refused(self) -> None:
        with pytest.raises(PermanentHandlerError, match="subject"):
            parse_analysis_payload(analysis_envelope(subject="BANANA"))

    def test_a_conversation_subject_is_refused(self) -> None:
        with pytest.raises(PermanentHandlerError, match="unknown"):
            parse_analysis_payload(analysis_envelope(subject="CONVERSATION"))

    def test_a_missing_subject_id_is_refused(self) -> None:
        body = analysis_envelope()
        del body["payload"]["metadata"]["subject_id"]  # type: ignore[index]

        with pytest.raises(PermanentHandlerError, match="subject_id"):
            parse_analysis_payload(body)  # type: ignore[arg-type]

    def test_a_malformed_uuid_is_refused(self) -> None:
        with pytest.raises(PermanentHandlerError, match="not a uuid"):
            parse_analysis_payload(analysis_envelope(subject_id="not-a-uuid"))


class TestQueuesDeclared:
    def test_both_queues_exist_in_the_topology(self) -> None:
        from loka.shared.infrastructure.broker.topology import QUEUES

        declared = {spec.name for spec in QUEUES}
        assert PAYMENTS_EVENTS_QUEUE in declared
        assert FRAUD_ANALYSIS_QUEUE in declared
