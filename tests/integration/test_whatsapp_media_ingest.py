"""The gateway hands media bytes to the platform and gets an object key back.

D3: Baileys could recognise an IMAGE, a DOCUMENT and a LOCATION but had nowhere
to put the bytes, so every photo was dropped as a message with no text. Housing
step 9 makes photos mandatory, Found needs the document, Dating needs the
profile picture — and MinIO sat in the compose file unused.

The endpoint is deliberately narrow: the shared gateway key is the only
credential, so what it accepts has to be exactly what WhatsApp can deliver and
nothing a browser would ever render.
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from typing import Any

import httpx
import pytest
import pytest_asyncio
from fastapi import FastAPI
from pydantic import SecretStr

from loka.interfaces.http.app import create_app
from loka.shared.infrastructure.config.settings import Settings
from loka.shared.infrastructure.object_storage.media import InMemoryMediaStorage

pytestmark = pytest.mark.integration

GATEWAY_KEY = "gateway-media-test-key"
# A one-pixel JPEG: the magic bytes matter, the picture does not.
JPEG = bytes.fromhex("ffd8ffe000104a46494600010100000100010000ffd9")


def auth(key: str = GATEWAY_KEY) -> dict[str, str]:
    return {"X-Api-Key": key}


@pytest_asyncio.fixture
async def api(integration_settings: Settings) -> AsyncIterator[tuple[httpx.AsyncClient, Any]]:
    settings = integration_settings.model_copy(
        update={
            "whatsapp": integration_settings.whatsapp.model_copy(
                update={"api_key": SecretStr(GATEWAY_KEY)}
            )
        }
    )
    app: FastAPI = create_app(settings)
    storage = InMemoryMediaStorage()
    async with app.router.lifespan_context(app):
        app.state.container.media_storage = storage
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://api") as http:
            yield http, storage


class TestAccepted:
    async def test_an_image_is_stored_and_its_key_returned(
        self, api: tuple[httpx.AsyncClient, Any]
    ) -> None:
        http, storage = api

        response = await http.post(
            "/integrations/whatsapp/media",
            content=JPEG,
            headers={
                **auth(),
                "content-type": "image/jpeg",
                "x-external-message-id": "3EB0ABCDEF123456",
            },
        )

        assert response.status_code == 201, response.text
        body = response.json()
        assert body["media_object_key"] in storage.objects
        assert body["size_bytes"] == len(JPEG)
        assert body["content_type"] == "image/jpeg"
        assert storage.objects[body["media_object_key"]][0] == JPEG

    async def test_the_key_lives_under_the_chat_prefix(
        self, api: tuple[httpx.AsyncClient, Any]
    ) -> None:
        """Chat media is not identity evidence and must not share its prefix."""
        http, _ = api

        response = await http.post(
            "/integrations/whatsapp/media",
            content=JPEG,
            headers={**auth(), "content-type": "image/jpeg", "x-external-message-id": "m1"},
        )

        assert response.json()["media_object_key"].startswith("chat/")

    async def test_the_same_message_redelivered_lands_in_the_same_folder(
        self, api: tuple[httpx.AsyncClient, Any]
    ) -> None:
        """A gateway retry must not scatter one photo across the bucket."""
        http, _ = api
        headers = {
            **auth(),
            "content-type": "image/jpeg",
            "x-external-message-id": "3EB0SAME",
        }

        first = await http.post("/integrations/whatsapp/media", content=JPEG, headers=headers)
        second = await http.post("/integrations/whatsapp/media", content=JPEG, headers=headers)

        folder = lambda key: key.rsplit("/", 1)[0]  # noqa: E731
        assert folder(first.json()["media_object_key"]) == folder(
            second.json()["media_object_key"]
        )

    async def test_the_phone_number_never_appears_in_the_key(
        self, api: tuple[httpx.AsyncClient, Any]
    ) -> None:
        """Keys reach logs and presigned URLs; a number in one is a leak."""
        http, _ = api

        response = await http.post(
            "/integrations/whatsapp/media",
            content=JPEG,
            headers={
                **auth(),
                "content-type": "image/jpeg",
                "x-external-message-id": "m2",
                "x-sender-phone": "237690000000",
            },
        )

        assert "690000000" not in response.json()["media_object_key"]


class TestRefused:
    async def test_a_missing_key_is_unauthorized(
        self, api: tuple[httpx.AsyncClient, Any]
    ) -> None:
        http, storage = api

        response = await http.post(
            "/integrations/whatsapp/media", content=JPEG, headers={"content-type": "image/jpeg"}
        )

        assert response.status_code == 401
        assert storage.objects == {}

    async def test_a_wrong_key_is_unauthorized(
        self, api: tuple[httpx.AsyncClient, Any]
    ) -> None:
        http, storage = api

        response = await http.post(
            "/integrations/whatsapp/media",
            content=JPEG,
            headers={**auth("wrong"), "content-type": "image/jpeg"},
        )

        assert response.status_code == 401
        assert storage.objects == {}

    async def test_an_empty_body_is_refused(
        self, api: tuple[httpx.AsyncClient, Any]
    ) -> None:
        http, storage = api

        response = await http.post(
            "/integrations/whatsapp/media",
            content=b"",
            headers={**auth(), "content-type": "image/jpeg"},
        )

        assert response.status_code == 422
        assert storage.objects == {}

    async def test_a_renderable_content_type_is_refused(
        self, api: tuple[httpx.AsyncClient, Any]
    ) -> None:
        """An upload endpoint that accepts HTML is a stored-XSS waiting for a reader."""
        http, storage = api

        response = await http.post(
            "/integrations/whatsapp/media",
            content=b"<script>alert(1)</script>",
            headers={**auth(), "content-type": "text/html"},
        )

        assert response.status_code == 415
        assert storage.objects == {}

    async def test_a_body_beyond_the_limit_is_refused(
        self, api: tuple[httpx.AsyncClient, Any]
    ) -> None:
        http, storage = api
        oversized = b"\xff\xd8" + b"0" * (17 * 1024 * 1024)

        response = await http.post(
            "/integrations/whatsapp/media",
            content=oversized,
            headers={**auth(), "content-type": "image/jpeg"},
        )

        assert response.status_code == 413
        assert storage.objects == {}
