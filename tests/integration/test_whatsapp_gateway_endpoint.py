"""The gateway ingress: admission, authentication and envelope shape.

These deliveries arrive from a transport that retries, so the endpoint has two
opposite obligations: never accept something the domain cannot use, and never
answer 500 to a body that is merely unlucky. What is asserted below is the
contract the Baileys sidecar codes against.
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import AbstractAsyncContextManager
from typing import Any

import httpx
import pytest
import pytest_asyncio
from pydantic import SecretStr

from loka.interfaces.http.app import create_app
from loka.shared.infrastructure.broker.topology import EVENTS_EXCHANGE
from loka.shared.infrastructure.config.settings import Settings

pytestmark = pytest.mark.integration

GATEWAY_KEY = "integration-gateway-key"
BASE_URL = "http://api"

DELIVERY: dict[str, Any] = {
    "provider": "baileys",
    "external_message_id": "3EB0ABCDEF123456",
    "conversation_ref": "237690000000@s.whatsapp.net",
    "sender_phone": "690000000",
    "message_kind": "TEXT",
    "provider_timestamp": "2026-03-01T09:00:00+00:00",
    "text": "Je veux publier un appartement a Bastos",
    "metadata": {"session_name": "loka-main"},
}


class RecordingBroker:
    def __init__(self) -> None:
        self.published: list[dict[str, Any]] = []

    async def connect(self) -> None:
        return None

    async def publish(
        self,
        *,
        routing_key: str,
        payload: dict[str, Any],
        exchange: str = EVENTS_EXCHANGE,
        headers: dict[str, Any] | None = None,
    ) -> None:
        self.published.append(
            {
                "routing_key": routing_key,
                "payload": payload,
                "exchange": exchange,
                "headers": headers or {},
            }
        )


class UnreachableBroker:
    async def connect(self) -> None:
        raise OSError("broker is down")


class FailingBroker(RecordingBroker):
    async def publish(self, **kwargs: Any) -> None:
        raise OSError("publish failed")


class Gateway:
    """The app under test, pointed at a recording broker."""

    def __init__(
        self,
        integration_settings: Settings,
        *,
        api_key: str = GATEWAY_KEY,
    ) -> None:
        settings = integration_settings.model_copy(
            update={
                "whatsapp": integration_settings.whatsapp.model_copy(
                    update={"api_key": SecretStr(api_key)}
                )
            }
        )
        self.broker = RecordingBroker()
        self.app = create_app(settings)
        self.http = httpx.AsyncClient(
            transport=httpx.ASGITransport(app=self.app), base_url=BASE_URL
        )
        self._lifespan: AbstractAsyncContextManager[None] = (
            self.app.router.lifespan_context(self.app)
        )

    async def __aenter__(self) -> Gateway:
        await self._lifespan.__aenter__()
        # Installed after startup, which is what production does too: the
        # lifespan puts a real Broker there and the endpoint reconnects lazily.
        self.app.state.container.broker = self.broker  # type: ignore[assignment]
        await self.http.__aenter__()
        return self

    async def __aexit__(self, *exc_info: object) -> Any:
        await self.http.__aexit__(*exc_info)
        return await self._lifespan.__aexit__(*exc_info)


@pytest_asyncio.fixture
async def gateway(integration_settings: Settings) -> AsyncIterator[Gateway]:
    async with Gateway(integration_settings) as running:
        yield running


def auth(key: str = GATEWAY_KEY) -> dict[str, str]:
    return {"X-Api-Key": key}


class TestAccepted:
    async def test_the_delivery_is_queued(self, gateway: Gateway) -> None:
        response = await gateway.http.post(
            "/integrations/whatsapp/events", json=DELIVERY, headers=auth()
        )

        assert response.status_code == 202, response.text
        body = response.json()
        assert body["accepted"] is True
        assert body["external_message_id"] == DELIVERY["external_message_id"]
        assert len(gateway.broker.published) == 1

    async def test_the_published_envelope_wraps_the_delivery(
        self, gateway: Gateway
    ) -> None:
        response = await gateway.http.post(
            "/integrations/whatsapp/events", json=DELIVERY, headers=auth()
        )

        published = gateway.broker.published[0]
        assert published["routing_key"] == "WhatsAppMessageReceived"
        assert published["exchange"] == EVENTS_EXCHANGE
        assert published["payload"]["event_type"] == "WhatsAppMessageReceived"
        assert published["payload"]["message_id"] == response.json()["message_id"]

        inner = published["payload"]["payload"]
        assert inner["text"] == DELIVERY["text"]
        assert inner["sender_phone"] == DELIVERY["sender_phone"]
        assert inner["external_message_id"] == DELIVERY["external_message_id"]

        assert published["headers"]["x-event-type"] == "WhatsAppMessageReceived"
        assert published["headers"]["message_id"] == published["payload"]["message_id"]

    async def test_a_media_only_delivery_is_accepted(self, gateway: Gateway) -> None:
        delivery = {**DELIVERY, "text": None, "media_object_key": "inbound/1.jpg"}

        response = await gateway.http.post(
            "/integrations/whatsapp/events", json=delivery, headers=auth()
        )

        assert response.status_code == 202, response.text
        inner = gateway.broker.published[0]["payload"]["payload"]
        assert inner["media_object_key"] == "inbound/1.jpg"

    async def test_two_deliveries_are_not_merged_by_the_transport(
        self, gateway: Gateway
    ) -> None:
        """De-duplication belongs to ingestion, not to the admission point."""
        first = await gateway.http.post(
            "/integrations/whatsapp/events", json=DELIVERY, headers=auth()
        )
        second = await gateway.http.post(
            "/integrations/whatsapp/events", json=DELIVERY, headers=auth()
        )

        assert first.json()["message_id"] != second.json()["message_id"]
        assert len(gateway.broker.published) == 2


class TestAuthentication:
    async def test_a_missing_key_is_unauthorized(self, gateway: Gateway) -> None:
        response = await gateway.http.post(
            "/integrations/whatsapp/events", json=DELIVERY
        )

        assert response.status_code == 401
        assert response.json()["code"] == "unauthenticated"
        assert gateway.broker.published == []

    async def test_a_wrong_key_is_unauthorized(self, gateway: Gateway) -> None:
        response = await gateway.http.post(
            "/integrations/whatsapp/events", json=DELIVERY, headers=auth("not-the-key")
        )

        assert response.status_code == 401
        assert gateway.broker.published == []


class TestFailClosed:
    async def test_an_unconfigured_key_refuses_the_endpoint(
        self, integration_settings: Settings
    ) -> None:
        async with Gateway(integration_settings, api_key="") as running:
            response = await running.http.post(
                "/integrations/whatsapp/events", json=DELIVERY, headers=auth("")
            )
            assert response.status_code == 503
            assert response.json()["code"] == "external_service_unavailable"
            assert running.broker.published == []


class TestRefused:
    async def test_a_delivery_with_neither_text_nor_media_is_refused(
        self, gateway: Gateway
    ) -> None:
        delivery = {**DELIVERY, "text": "   ", "media_object_key": None}

        response = await gateway.http.post(
            "/integrations/whatsapp/events", json=delivery, headers=auth()
        )

        assert response.status_code == 422
        assert response.json()["code"] == "validation_failed"
        assert gateway.broker.published == []

    async def test_a_missing_required_field_is_rejected(self, gateway: Gateway) -> None:
        delivery = {key: value for key, value in DELIVERY.items() if key != "sender_phone"}

        response = await gateway.http.post(
            "/integrations/whatsapp/events", json=delivery, headers=auth()
        )

        assert response.status_code == 422
        assert gateway.broker.published == []


class TestBrokerDegradation:
    async def test_an_unreachable_broker_is_a_retryable_503(
        self, integration_settings: Settings
    ) -> None:
        async with Gateway(integration_settings) as running:
            running.app.state.container.broker = UnreachableBroker()  # type: ignore[assignment]

            response = await running.http.post(
                "/integrations/whatsapp/events", json=DELIVERY, headers=auth()
            )

            assert response.status_code == 503
            assert response.json()["code"] == "external_service_unavailable"

    async def test_a_failed_publish_is_reported_as_retryable(
        self, gateway: Gateway
    ) -> None:
        gateway.app.state.container.broker = FailingBroker()  # type: ignore[assignment]

        response = await gateway.http.post(
            "/integrations/whatsapp/events", json=DELIVERY, headers=auth()
        )

        assert response.status_code == 503
        assert response.json()["code"] == "external_service_unavailable"
        assert gateway.broker.published == []
