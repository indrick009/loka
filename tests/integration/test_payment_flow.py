"""Payment lifecycle over HTTP: initiate, signed webhook, status, replay.

The webhook signature is produced the way the mock provider contract requires:
an HMAC-SHA256 hex digest of the raw body using the configured webhook secret.
"""

from __future__ import annotations

import json
import secrets
import uuid
from collections.abc import AsyncIterator
from datetime import UTC, datetime, timedelta

import httpx
import pytest
import pytest_asyncio
from fastapi import FastAPI
from sqlalchemy import select

from loka.bounded_contexts.identity.infrastructure.persistence.models import RefreshTokenRow
from loka.bounded_contexts.payment.infrastructure.persistence.models import (
    ServiceAccessGrantRow,
    ServiceFeePaymentRow,
)
from loka.interfaces.http.app import create_app
from loka.interfaces.http.auth import hash_token
from loka.shared.application.ports import SignatureVerifier
from loka.shared.infrastructure.config.settings import Settings
from loka.shared.infrastructure.db.engine import Database

pytestmark = pytest.mark.integration

PROVIDER = "mock"


def _auth(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


@pytest_asyncio.fixture
async def client(integration_settings: Settings) -> AsyncIterator[httpx.AsyncClient]:
    app: FastAPI = create_app(integration_settings)
    async with app.router.lifespan_context(app):
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://api") as http:
            yield http


async def _token_for(database: Database, user_id: uuid.UUID) -> str:
    token = secrets.token_urlsafe(32)
    issued_at = datetime.now(UTC)
    async with database.session() as session:
        session.add(
            RefreshTokenRow(
                id=uuid.uuid4(),
                user_id=user_id,
                token_hash=hash_token(token),
                device_label="pytest-payments",
                issued_at=issued_at,
                expires_at=issued_at + timedelta(days=30),
                revoked_at=None,
                is_admin_session=False,
            )
        )
    return token


def _signed_callback_body(
    secret: str,
    *,
    reference: str,
    status: str = "SUCCEEDED",
    event_id: str = "evt-1",
) -> tuple[bytes, str]:
    payload = {
        "event": {"id": event_id, "type": "payment.callback"},
        "data": {
            "provider_reference": reference,
            "status": status,
            "timestamp": datetime.now(UTC).isoformat(),
        },
    }
    body = json.dumps(payload).encode()
    return body, SignatureVerifier.sign(body, secret)


async def _lifecycle(
    client: httpx.AsyncClient,
    database: Database,
    secret: str,
    *,
    application_id: uuid.UUID | None = None,
) -> tuple[str, dict]:
    user_id, landlord_id = uuid.uuid4(), uuid.uuid4()
    token = await _token_for(database, user_id)
    application_id = application_id or uuid.uuid4()

    initiated = await client.post(
        f"/payments/applications/{application_id}/initiate",
        json={"landlord_id": str(landlord_id)},
        headers=_auth(token),
    )
    assert initiated.status_code == 201, initiated.text
    view = initiated.json()
    assert view["status"] == "PENDING_CONFIRMATION"

    body, signature = _signed_callback_body(secret, reference=view["provider_reference"])
    settled = await client.post(
        f"/webhooks/payments/{PROVIDER}",
        content=body,
        headers={"x-loka-signature": signature, "content-type": "application/json"},
    )
    assert settled.status_code == 200, settled.text
    assert settled.json()["payment"]["status"] == "SUCCEEDED"
    assert settled.json()["grant"]["status"] == "ACTIVE"

    status_response = await client.get(
        f"/payments/applications/{application_id}", headers=_auth(token)
    )
    assert status_response.status_code == 200
    return token, status_response.json()


async def test_the_payment_lifecycle_settles_and_grants_access(
    client: httpx.AsyncClient,
    database: Database,
    integration_settings: Settings,
) -> None:
    secret = integration_settings.payment.webhook_secret.get_secret_value()
    _token, status = await _lifecycle(client, database, secret)

    assert status["payment"]["settled"] is True
    assert status["grant"]["status"] == "ACTIVE"

    async with database.session() as session:
        payment_row = (await session.execute(select(ServiceFeePaymentRow))).scalar_one()
        grant_row = (await session.execute(select(ServiceAccessGrantRow))).scalar_one()
    assert payment_row.status == "SUCCEEDED"
    assert payment_row.callback_signature is not None
    assert grant_row.status == "ACTIVE"
    assert grant_row.payment_id == payment_row.id


async def test_a_replayed_webhook_is_acknowledged_without_a_second_grant(
    client: httpx.AsyncClient,
    database: Database,
    integration_settings: Settings,
) -> None:
    secret = integration_settings.payment.webhook_secret.get_secret_value()
    application_id = uuid.uuid4()
    user_id, landlord_id = uuid.uuid4(), uuid.uuid4()
    token = await _token_for(database, user_id)

    initiated = await client.post(
        f"/payments/applications/{application_id}/initiate",
        json={"landlord_id": str(landlord_id)},
        headers=_auth(token),
    )
    reference = initiated.json()["provider_reference"]
    body, signature = _signed_callback_body(secret, reference=reference)

    first = await client.post(
        f"/webhooks/payments/{PROVIDER}",
        content=body,
        headers={"x-loka-signature": signature},
    )
    second = await client.post(
        f"/webhooks/payments/{PROVIDER}",
        content=body,
        headers={"x-loka-signature": signature},
    )

    assert first.status_code == 200
    assert first.json()["replayed"] is False
    assert second.status_code == 200
    assert second.json()["replayed"] is True

    async with database.session() as session:
        grants = (await session.execute(select(ServiceAccessGrantRow))).scalars().all()
    assert len(grants) == 1


async def test_a_forged_signature_is_rejected(
    client: httpx.AsyncClient,
    database: Database,
) -> None:
    await _token_for(database, uuid.uuid4())
    body = json.dumps({"event": {"id": "evt-x"}, "data": {"status": "SUCCEEDED"}}).encode()

    response = await client.post(
        f"/webhooks/payments/{PROVIDER}",
        content=body,
        headers={"x-loka-signature": "deadbeef" * 8},
    )

    assert response.status_code == 422
    assert response.json()["code"] == "callback_rejected"


async def test_a_settled_application_refuses_a_second_payment(
    client: httpx.AsyncClient,
    database: Database,
    integration_settings: Settings,
) -> None:
    secret = integration_settings.payment.webhook_secret.get_secret_value()
    application_id = uuid.uuid4()
    user_id, landlord_id = uuid.uuid4(), uuid.uuid4()
    token = await _token_for(database, user_id)
    await client.post(
        f"/payments/applications/{application_id}/initiate",
        json={"landlord_id": str(landlord_id)},
        headers=_auth(token),
    )

    async with database.session() as session:
        reference = (
            await session.execute(select(ServiceFeePaymentRow))
        ).scalar_one().provider_reference

    body, signature = _signed_callback_body(secret, reference=reference)
    await client.post(
        f"/webhooks/payments/{PROVIDER}",
        content=body,
        headers={"x-loka-signature": signature},
    )

    second = await client.post(
        f"/payments/applications/{application_id}/initiate",
        json={"landlord_id": str(landlord_id)},
        headers=_auth(token),
    )

    assert second.status_code == 422
    assert second.json()["code"] == "payment_already_settled"


async def test_the_webhook_is_unauthenticated_by_design(
    client: httpx.AsyncClient,
    database: Database,
) -> None:
    # No bearer token, no landlord: the provider callback must still be reachable.
    await _token_for(database, uuid.uuid4())
    body = json.dumps({"event": {"id": "evt-y"}, "data": {"status": "SUCCEEDED"}}).encode()

    response = await client.post(
        f"/webhooks/payments/{PROVIDER}",
        content=body,
        headers={"x-loka-signature": "deadbeef" * 8},
    )

    assert response.status_code == 422  # rejected for the signature, not for auth