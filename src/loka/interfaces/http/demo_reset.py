"""DEMO ONLY — the ``_empty_all_data`` WhatsApp command forgets the two test numbers.

Sent by any number, it removes everything that belongs to 692617975 and
658528664 — their accounts, conversations, listings, restaurants, profiles,
declarations, bookings and payments — so a demo can be replayed from scratch.
Everyone else's data stays, the demo Yaoundé (+237 600 000 xxx) included. It
is off unless ``DEMO_RESET_ENABLED=true``.

To remove it: delete this file and the ``demo_reset`` lines in
``routers/whatsapp.py`` and ``settings.py``.
"""

from __future__ import annotations

import uuid
from collections.abc import Sequence

import httpx
from sqlalchemy import bindparam, text
from sqlalchemy.dialects.postgresql import ARRAY, UUID
from sqlalchemy.ext.asyncio import AsyncSession

from loka.interfaces.http.container import Container
from loka.shared.infrastructure.logging import get_logger

COMMAND = "_empty_all_data"
TEST_PHONES = ("+237692617975", "+237658528664")

_logger = get_logger("demo_reset")

Targets = list[tuple[str, tuple[str, ...]]]

# Which rows a set of ids owns: (table, columns any of which may hold one).
_BY_USER: Targets = [
    ("ai_usage_ledger", ("user_id",)), ("audit_log", ("actor_id",)),
    ("refresh_tokens", ("user_id",)), ("phone_verification_challenges", ("user_id",)),
    ("idempotency_keys", ("owner_id",)), ("reports", ("reporter_id",)),
    ("risk_profiles", ("subject_id",)), ("fraud_signals", ("subject_id",)),
    ("tenant_demands", ("tenant_id",)), ("property_availability_checks", ("tenant_id",)),
    ("service_access_grants", ("tenant_id",)), ("service_fee_payments", ("tenant_id",)),
    ("food_requests", ("client_user_id",)), ("found_claims", ("owner_user_id",)),
    ("booking_reservations", ("client_user_id",)),
    ("contact_unlocks", ("payer_user_id", "target_user_id")),
    ("provider_reviews", ("author_id",)), ("provider_complaints", ("complainant_id",)),
]
_BY_SESSION: Targets = [
    ("conversation_turns", ("session_id",)), ("decision_shadows", ("session_id",)),
    ("inbound_messages", ("session_id",)), ("outbound_messages", ("session_id",)),
    ("conversation_sessions", ("id",)),
]
_BY_LANDLORD: Targets = [
    ("media_hashes", ("landlord_id",)), ("property_availability_checks", ("landlord_id",)),
    ("property_search_documents", ("landlord_id",)), ("service_access_grants", ("landlord_id",)),
    ("service_fee_payments", ("landlord_id",)), ("trust_profiles", ("landlord_id",)),
    ("properties", ("landlord_id",)), ("landlord_profiles", ("id",)),
]
_BY_PROPERTY: Targets = [
    ("property_media", ("property_id",)), ("property_data_quality_issues", ("property_id",)),
]
_BY_PROVIDER: Targets = [
    ("provider_reviews", ("provider_id",)), ("provider_complaints", ("provider_id",)),
    ("provider_standings", ("provider_id",)),
]
_BY_RESTAURANT: Targets = [
    ("food_requests", ("restaurant_id",)), ("food_daily_menus", ("restaurant_id",)),
    ("food_restaurants", ("id",)),
]
_BY_PROFILE: Targets = [
    ("dating_answers", ("from_profile", "to_profile")),
    ("dating_talks", ("first_profile", "second_profile")), ("dating_profiles", ("id",)),
]
_BY_DECLARATION: Targets = [("found_claims", ("declaration_id",)), ("found_declarations", ("id",))]
_BY_AGENCY: Targets = [
    ("booking_reservations", ("agency_id",)), ("booking_lines", ("agency_id",)),
    ("booking_agencies", ("id",)),
]
_BY_PHONE = (
    ("inbound_messages", "sender_phone"), ("outbound_messages", "recipient_phone"),
    ("booking_reservations", "payer_phone"), ("contact_unlocks", "payer_phone"),
    ("phone_verification_challenges", "phone_e164"),
)


def is_demo_reset(container: Container, message: str | None) -> bool:
    return container.settings.demo_reset_enabled and (message or "").strip() == COMMAND


async def reset_everything(container: Container, phone: str) -> None:
    async with container.database.session() as session:
        removed = await forget_test_numbers(session)
    if container.redis is not None:
        for number in TEST_PHONES:
            async for key in container.redis.scan_iter(match=f"*{number[4:]}*"):
                await container.redis.delete(key)
    _logger.warning("demo_reset_done", rows=removed)
    await _confirm(container, phone)


async def forget_test_numbers(session: AsyncSession) -> int:
    """Every row of the two test numbers, and only theirs."""
    phones = list(TEST_PHONES)
    users = await _ids(session, "SELECT id FROM users WHERE phone_e164 = ANY(:p)", phones)
    sessions = await _ids(
        session,
        "SELECT id FROM conversation_sessions WHERE phone_e164 = ANY(:p) OR user_id = ANY(:u)",
        phones,
        users,
    )
    owned = "SELECT id FROM {} WHERE {} = ANY(:u)"
    landlords = await _ids(session, owned.format("landlord_profiles", "user_id"), phones, users)
    properties = await _ids(session, owned.format("properties", "landlord_id"), phones, landlords)
    restaurants = await _ids(
        session, owned.format("food_restaurants", "owner_user_id"), phones, users
    )
    profiles = await _ids(session, owned.format("dating_profiles", "user_id"), phones, users)
    declarations = await _ids(
        session, owned.format("found_declarations", "finder_user_id"), phones, users
    )
    agencies = await _ids(session, "SELECT id FROM booking_agencies WHERE phone = ANY(:p)", phones)

    removed = await _delete(session, _BY_USER, users)
    removed += await _delete(session, _BY_SESSION, sessions)
    removed += await _delete(session, _BY_PROPERTY, properties)
    removed += await _delete(session, _BY_LANDLORD, landlords)
    removed += await _delete(session, _BY_PROVIDER, [*landlords, *restaurants, *agencies])
    removed += await _delete(session, _BY_RESTAURANT, restaurants)
    removed += await _delete(session, _BY_PROFILE, profiles)
    removed += await _delete(session, _BY_DECLARATION, declarations)
    removed += await _delete(session, _BY_AGENCY, agencies)
    for table, column in _BY_PHONE:
        result = await session.execute(
            text(f"DELETE FROM {table} WHERE {column} = ANY(:p)"), {"p": phones}
        )
        removed += _count(result)
    removed += await _delete(session, [("users", ("id",))], users)
    return removed


async def _ids(
    session: AsyncSession, query: str, phones: list[str], ids: Sequence[uuid.UUID] = ()
) -> list[uuid.UUID]:
    statement = text(query)
    params: dict[str, object] = {}
    if ":p" in query:
        params["p"] = phones
    if ":u" in query:
        statement = statement.bindparams(bindparam("u", type_=ARRAY(UUID(as_uuid=True))))
        params["u"] = list(ids)
    return list((await session.execute(statement, params)).scalars().all())


async def _delete(session: AsyncSession, targets: Targets, ids: list[uuid.UUID]) -> int:
    if not ids:
        return 0
    removed = 0
    for table, columns in targets:
        where = " OR ".join(f"{column} = ANY(:ids)" for column in columns)
        statement = text(f"DELETE FROM {table} WHERE {where}").bindparams(
            bindparam("ids", type_=ARRAY(UUID(as_uuid=True)))
        )
        removed += _count(await session.execute(statement, {"ids": ids}))
    return removed


def _count(result: object) -> int:
    return int(getattr(result, "rowcount", 0) or 0)


async def _confirm(container: Container, phone: str) -> None:
    whatsapp = container.settings.whatsapp
    api_key = whatsapp.api_key.get_secret_value()
    async with httpx.AsyncClient(
        base_url=whatsapp.gateway_url.rstrip("/"),
        timeout=whatsapp.request_timeout_seconds,
        headers={"X-Api-Key": api_key} if api_key else {},
    ) as client:
        # A failed confirmation must not fail the request: the gateway would
        # redeliver the command and wipe a second time.
        try:
            await client.post(
                "/send",
                json={
                    "to": phone,
                    "text": "Démo : les données des numéros de test sont effacées. "
                    "Les données de démonstration restent.",
                },
            )
        except httpx.HTTPError as exc:
            _logger.warning("demo_reset_confirm_failed", error=type(exc).__name__)
