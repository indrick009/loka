"""A WhatsApp position pin is content, not an empty message.

The four Kora specifications all name shared location as the preferred way to
locate something, so a delivery that carries only a pin has to survive
admission. It used to be dropped at the gateway as ``no_text``.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime

import pytest

from loka.bounded_contexts.identity.domain.value_objects.phone_number import PhoneNumber
from loka.bounded_contexts.messaging.domain.value_objects.inbound_message import (
    InboundMessage,
    MessageKind,
    SharedLocation,
)
from loka.shared.domain.errors import ValidationFailed

NOW = datetime(2026, 3, 1, 9, 0, tzinfo=UTC)
# Bastos, Yaoundé.
BASTOS = (3.8905, 11.5139)


def _message(**overrides: object) -> InboundMessage:
    payload: dict[str, object] = {
        "message_id": uuid.uuid4(),
        "provider": "baileys",
        "external_message_id": "BAIL_LOC_1",
        "conversation_ref": "699123456@s.whatsapp.net",
        "sender_phone": PhoneNumber.normalize("699123456"),
        "kind": MessageKind.LOCATION,
        "provider_timestamp": NOW,
        "received_at": NOW,
    }
    payload.update(overrides)
    return InboundMessage(**payload)  # type: ignore[arg-type]


class TestSharedLocation:
    def test_a_pin_inside_the_world_is_accepted(self) -> None:
        point = SharedLocation(latitude=BASTOS[0], longitude=BASTOS[1])
        assert point.latitude == BASTOS[0]
        assert point.longitude == BASTOS[1]
        assert point.label is None

    def test_a_place_share_keeps_the_name_whatsapp_sent(self) -> None:
        point = SharedLocation(
            latitude=BASTOS[0], longitude=BASTOS[1], label="  Bastos, Yaoundé  "
        )
        assert point.label == "Bastos, Yaoundé"

    @pytest.mark.parametrize(
        ("latitude", "longitude"),
        [(91.0, 11.0), (-91.0, 11.0), (3.0, 181.0), (3.0, -181.0)],
    )
    def test_a_pin_outside_the_world_is_refused(
        self, latitude: float, longitude: float
    ) -> None:
        """A provider that sends nonsense must not become a listing nobody can find."""
        with pytest.raises(ValidationFailed):
            SharedLocation(latitude=latitude, longitude=longitude)

    def test_a_pin_at_the_null_island_is_refused(self) -> None:
        """(0, 0) is what a dropped field looks like, never where somebody lives."""
        with pytest.raises(ValidationFailed):
            SharedLocation(latitude=0.0, longitude=0.0)


class TestAdmission:
    def test_a_message_carrying_only_a_pin_is_admitted(self) -> None:
        message = _message(
            location=SharedLocation(latitude=BASTOS[0], longitude=BASTOS[1])
        )
        assert message.has_location is True
        assert message.has_text is False
        assert message.has_media is False

    def test_a_message_carrying_only_media_is_still_admitted(self) -> None:
        message = _message(kind=MessageKind.IMAGE, media_object_key="chat/x/y.jpg")
        assert message.has_media is True

    def test_a_message_carrying_nothing_at_all_is_still_refused(self) -> None:
        with pytest.raises(ValidationFailed, match="text, a media object or a location"):
            _message()
