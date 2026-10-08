"""Landlord verification over HTTP.

The submission path is tenant-facing, the decision path is admin-only. Both go
through the real app, authenticator and use cases.
"""

from __future__ import annotations

import uuid
from collections.abc import AsyncIterator
from datetime import UTC, datetime, timedelta

import httpx
import pytest
import pytest_asyncio
from fastapi import FastAPI

from loka.bounded_contexts.identity.infrastructure.persistence.models import RefreshTokenRow
from loka.interfaces.http.app import create_app
from loka.interfaces.http.auth import hash_token
from loka.shared.infrastructure.config.settings import Settings
from loka.shared.infrastructure.db.engine import Database

pytestmark = pytest.mark.integration

TENANT_TOKEN = "landlord-tenant-token-0123456789"
ADMIN_TOKEN = "landlord-admin-token-0123456789"


@pytest_asyncio.fixture
async def client(integration_settings: Settings) -> AsyncIterator[httpx.AsyncClient]:
    app: FastAPI = create_app(integration_settings)
    async with app.router.lifespan_context(app):
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://api") as http:
            yield http


async def _issue_token(
    database: Database, user_id: uuid.UUID, *, value: str, is_admin: bool = False
) -> None:
    issued_at = datetime.now(UTC)
    async with database.session() as session:
        session.add(
            RefreshTokenRow(
                id=uuid.uuid4(),
                user_id=user_id,
                token_hash=hash_token(value),
                device_label="pytest",
                issued_at=issued_at,
                expires_at=issued_at + timedelta(days=30),
                revoked_at=None,
                is_admin_session=is_admin,
            )
        )


def _auth(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


def _submit_body() -> dict[str, object]:
    return {
        "document_kind": "NATIONAL_ID_CARD",
        "document_object_key": "identity/cni.jpg",
        "document_checksum": "a" * 64,
        "selfie_object_key": "identity/selfie.jpg",
        "selfie_checksum": "b" * 64,
    }


async def test_the_submit_then_review_journey_over_http(
    client: httpx.AsyncClient, database: Database
) -> None:
    tenant_id = uuid.uuid4()
    await _issue_token(database, tenant_id, value=TENANT_TOKEN)

    requested = await client.post(
        "/landlords/verification/request",
        json={"display_name": "Awa N."},
        headers=_auth(TENANT_TOKEN),
    )
    assert requested.status_code == 200, requested.text
    assert requested.json()["status"] == "PENDING"

    submitted = await client.post(
        "/landlords/verification/submit",
        json=_submit_body(),
        headers=_auth(TENANT_TOKEN),
    )
    assert submitted.status_code == 200, submitted.text
    body = submitted.json()
    assert body["status"] == "UNDER_REVIEW"
    assert body["missing_evidence"] == []
    request_id = body["request_id"]

    admin_id = uuid.uuid4()
    await _issue_token(database, admin_id, value=ADMIN_TOKEN, is_admin=True)

    forbidden = await client.post(
        f"/landlords/verification/{request_id}/review",
        json={"decision": "APPROVE"},
        headers=_auth(TENANT_TOKEN),
    )
    assert forbidden.status_code == 403
    assert forbidden.json()["code"] == "authorization_denied"

    approved = await client.post(
        f"/landlords/verification/{request_id}/review",
        json={"decision": "APPROVE", "notes": "CNI lisible"},
        headers=_auth(ADMIN_TOKEN),
    )
    assert approved.status_code == 200, approved.text
    assert approved.json()["status"] == "VERIFIED"
    assert approved.json()["can_publish"] is True

    status = await client.get(
        "/landlords/verification",
        headers=_auth(TENANT_TOKEN),
    )
    assert status.status_code == 200
    assert status.json()["can_publish"] is True


async def test_submitting_without_a_token_is_unauthorized(
    client: httpx.AsyncClient, database: Database
) -> None:
    response = await client.post("/landlords/verification/submit", json=_submit_body())

    assert response.status_code == 401
    assert response.json()["code"] == "unauthenticated"