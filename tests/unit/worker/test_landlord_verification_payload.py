"""The landlord verification queue's payload contract."""

from __future__ import annotations

import uuid

import pytest

from loka.interfaces.worker.landlord_verification_consumer import (
    LANDLORD_VERIFICATION_QUEUE,
    parse_verification_request_id,
)
from loka.shared.infrastructure.broker.consumer import PermanentHandlerError
from loka.shared.infrastructure.broker.topology import QUEUES

pytestmark = pytest.mark.unit


def test_the_queue_binds_to_the_submitted_event() -> None:
    spec = next(item for item in QUEUES if item.name == LANDLORD_VERIFICATION_QUEUE)
    assert spec.routing_keys == ("LandlordVerificationSubmitted",)


def test_a_valid_envelope_yields_the_request_id() -> None:
    request_id = uuid.uuid4()
    payload = {
        "event_type": "LandlordVerificationSubmitted",
        "payload": {"request_id": str(request_id)},
    }

    assert parse_verification_request_id(payload) == request_id


@pytest.mark.parametrize(
    "payload",
    [
        {},
        {"payload": None},
        {"payload": {}},
        {"payload": {"request_id": ""}},
        {"payload": {"request_id": "not-a-uuid"}},
    ],
)
def test_a_malformed_envelope_is_refused_permanently(payload: dict[str, object]) -> None:
    with pytest.raises(PermanentHandlerError):
        parse_verification_request_id(payload)