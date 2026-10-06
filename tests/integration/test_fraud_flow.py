"""Fraud and trust lifecycle over HTTP.

A report is the human signal: submitting it immediately re-evaluates the
target so the escalation queue reflects fresh evidence, dismissing it lowers
the score again, and clearing is an analyst decision. Scores are
recommendations — the whole end-to-end path is admin-only.
"""

from __future__ import annotations

import secrets
import uuid
from collections.abc import AsyncIterator
from datetime import UTC, datetime, timedelta

import httpx
import pytest_asyncio
from fastapi import FastAPI

from loka.bounded_contexts.identity.infrastructure.persistence.models import RefreshTokenRow
from loka.interfaces.http.app import create_app
from loka.interfaces.http.auth import hash_token
from loka.shared.infrastructure.config.settings import Settings
from loka.shared.infrastructure.db.engine import Database

pytestmark = pytest.mark.integration


def _auth(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


@pytest_asyncio.fixture
async def client(integration_settings: Settings) -> AsyncIterator[httpx.AsyncClient]:
    app: FastAPI = create_app(integration_settings)
    async with app.router.lifespan_context(app):
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://api") as http:
            yield http


async def _token_for(database: Database, user_id: uuid.UUID, *, admin: bool = False) -> str:
    token = secrets.token_urlsafe(32)
    issued_at = datetime.now(UTC)
    async with database.session() as session:
        session.add(
            RefreshTokenRow(
                id=uuid.uuid4(),
                user_id=user_id,
                token_hash=hash_token(token),
                device_label="pytest-fraud",
                issued_at=issued_at,
                expires_at=issued_at + timedelta(days=30),
                revoked_at=None,
                is_admin_session=admin,
            )
        )
    return token


async def test_a_report_escalates_then_follows_the_evidence(
    client: httpx.AsyncClient,
    database: Database,
) -> None:
    landlord_id = uuid.uuid4()
    user_token = await _token_for(database, uuid.uuid4())
    admin_token = await _token_for(database, uuid.uuid4(), admin=True)

    missing = await client.get(
        f"/fraud/risk/LANDLORD/{landlord_id}", headers=_auth(admin_token)
    )
    assert missing.status_code == 404

    submitted = await client.post(
        "/reports",
        json={
            "target_type": "LANDLORD",
            "target_id": str(landlord_id),
            "reason": "SCAM",
            "description": "Asking for a deposit before showing the flat",
        },
        headers=_auth(user_token),
    )
    assert submitted.status_code == 201, submitted.text
    report_id = submitted.json()["report_id"]

    escalated = await client.get(
        f"/fraud/risk/LANDLORD/{landlord_id}", headers=_auth(admin_token)
    )
    assert escalated.status_code == 200, escalated.text
    risk = escalated.json()
    assert risk["score"] == 70
    assert risk["band"] == "HIGH"
    assert risk["under_manual_review"] is True
    assert "IDENTITY_UNVERIFIED" in risk["reasons"]
    assert "LISTING_REPORTED" in risk["reasons"]

    queue = await client.get("/fraud/escalations", headers=_auth(admin_token))
    assert queue.status_code == 200
    assert any(item["profile_id"] == risk["profile_id"] for item in queue.json()["items"])

    dismissed = await client.post(
        f"/reports/{report_id}/review",
        json={
            "action": "DISMISS",
            "resolution": "Phone checks confirmed a real agent",
        },
        headers=_auth(admin_token),
    )
    assert dismissed.status_code == 200, dismissed.text
    assert dismissed.json()["status"] == "DISMISSED"

    cooled = await client.get(
        f"/fraud/risk/LANDLORD/{landlord_id}", headers=_auth(admin_token)
    )
    assert cooled.status_code == 200
    assert cooled.json()["score"] == 40
    assert cooled.json()["band"] == "MEDIUM"

    cleared = await client.post(
        f"/fraud/risk/{risk['profile_id']}/review",
        json={"decision": "CLEAR", "note": "Identity documents verified"},
        headers=_auth(admin_token),
    )
    assert cleared.status_code == 200, cleared.text
    assert cleared.json()["score"] == 0
    assert cleared.json()["band"] == "LOW"

    summary = await client.get("/fraud/summary", headers=_auth(admin_token))
    assert summary.status_code == 200
    assert summary.json()["total_profiles"] == 1


async def test_reviewing_another_report_only_closes_that_report(
    client: httpx.AsyncClient,
    database: Database,
) -> None:
    landlord_id = uuid.uuid4()
    user_token = await _token_for(database, uuid.uuid4())
    admin_token = await _token_for(database, uuid.uuid4(), admin=True)

    first = await client.post(
        "/reports",
        json={
            "target_type": "LANDLORD",
            "target_id": str(landlord_id),
            "reason": "HIDDEN_FEE",
            "description": "The landlord demands an extra service fee",
        },
        headers=_auth(user_token),
    )
    await client.post(
        "/reports",
        json={
            "target_type": "LANDLORD",
            "target_id": str(landlord_id),
            "reason": "FAKE_LANDLORD",
            "description": "Profile photo does not match the agent",
        },
        headers=_auth(user_token),
    )
    assert first.status_code == 201

    listed = await client.get("/reports?status_filter=OPEN", headers=_auth(admin_token))
    assert listed.status_code == 200
    assert listed.json()["total"] == 2

    actioned = await client.post(
        f"/reports/{first.json()['report_id']}/review",
        json={
            "action": "ACTION",
            "resolution": "Reported to the identity team",
        },
        headers=_auth(admin_token),
    )
    assert actioned.status_code == 200
    assert actioned.json()["status"] == "ACTIONED"

    remaining = await client.get("/reports?status_filter=OPEN", headers=_auth(admin_token))
    assert remaining.status_code == 200
    assert remaining.json()["total"] == 1


async def test_analyst_routes_reject_regular_users(
    client: httpx.AsyncClient,
    database: Database,
) -> None:
    user_token = await _token_for(database, uuid.uuid4())

    read = await client.get("/fraud/summary", headers=_auth(user_token))
    assert read.status_code == 403
    assert read.json()["code"] == "authorization_denied"

    review = await client.post(
        f"/reports/{uuid.uuid4()}/review",
        json={"action": "DISMISS", "resolution": "n/a"},
        headers=_auth(user_token),
    )
    assert review.status_code == 403

    denied = await client.post(
        f"/fraud/risk/{uuid.uuid4()}/review",
        json={"decision": "LIFT"},
        headers=_auth(user_token),
    )
    assert denied.status_code == 403


async def test_analyst_routes_require_a_token(
    client: httpx.AsyncClient,
    database: Database,
) -> None:
    await _token_for(database, uuid.uuid4())

    response = await client.get("/fraud/summary")
    assert response.status_code == 401
    assert response.json()["code"] == "unauthenticated"


async def test_a_missing_property_cannot_be_scored(
    client: httpx.AsyncClient,
    database: Database,
) -> None:
    admin_token = await _token_for(database, uuid.uuid4(), admin=True)

    response = await client.post(
        f"/fraud/risk/PROPERTY/{uuid.uuid4()}/evaluate", headers=_auth(admin_token)
    )
    assert response.status_code == 404
    assert response.json()["code"] == "resource_not_found"