"""The analysis consumer's payload contract.

A malformed request is permanent: retrying it would fail identically for ever and
park the message in the DLQ for good. These cases pin down which payloads are
refused permanently so a bad producer is visible as a DLQ entry rather than a
retry loop that never drains.
"""

from __future__ import annotations

import uuid

import pytest

from loka.interfaces.worker.conversation_analysis_consumer import (
    AI_REQUEST_QUEUE,
    parse_analysis_payload,
)
from loka.shared.infrastructure.broker.consumer import PermanentHandlerError

pytestmark = pytest.mark.unit


def envelope(**metadata: object) -> dict[str, object]:
    base = {"message_id": str(uuid.uuid4()), "session_id": str(uuid.uuid4())}
    return {
        "event_id": str(uuid.uuid4()),
        "event_type": "ConversationAnalysisRequested",
        "payload": {"metadata": {**base, **metadata}},
    }


class TestAccepted:
    def test_the_ids_are_extracted(self) -> None:
        body = envelope()
        metadata = body["payload"]["metadata"]  # type: ignore[index]

        message_id, session_id = parse_analysis_payload(body)  # type: ignore[arg-type]

        assert str(message_id) == metadata["message_id"]
        assert str(session_id) == metadata["session_id"]

    def test_the_queue_is_the_declared_one(self) -> None:
        from loka.shared.infrastructure.broker.topology import QUEUES

        assert AI_REQUEST_QUEUE in {spec.name for spec in QUEUES}


class TestRefusedPermanently:
    def test_a_missing_payload_is_refused(self) -> None:
        with pytest.raises(PermanentHandlerError, match="payload"):
            parse_analysis_payload({})

    def test_a_payload_without_metadata_is_refused(self) -> None:
        with pytest.raises(PermanentHandlerError, match="metadata"):
            parse_analysis_payload({"payload": {}})

    def test_a_missing_message_id_is_refused(self) -> None:
        body = envelope()
        del body["payload"]["metadata"]["message_id"]  # type: ignore[index]

        with pytest.raises(PermanentHandlerError, match="message_id"):
            parse_analysis_payload(body)  # type: ignore[arg-type]

    def test_a_missing_session_id_is_refused(self) -> None:
        body = envelope()
        del body["payload"]["metadata"]["session_id"]  # type: ignore[index]

        with pytest.raises(PermanentHandlerError, match="session_id"):
            parse_analysis_payload(body)  # type: ignore[arg-type]

    def test_a_malformed_uuid_is_refused(self) -> None:
        with pytest.raises(PermanentHandlerError, match="not a uuid"):
            parse_analysis_payload(envelope(message_id="not-a-uuid"))

    def test_an_empty_message_id_is_refused(self) -> None:
        with pytest.raises(PermanentHandlerError, match="message_id"):
            parse_analysis_payload(envelope(message_id="   "))

    def test_a_null_id_is_refused(self) -> None:
        with pytest.raises(PermanentHandlerError, match="session_id"):
            parse_analysis_payload(envelope(session_id=None))
