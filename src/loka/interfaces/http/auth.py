"""Bearer token authentication for the HTTP API.

Tokens are opaque random strings and only their SHA-256 hash is stored, so a
database leak cannot be replayed against the API. The authenticated principal is
the *only* accepted source of ``actor_id``: taking it from a body or query
parameter would let any caller act on someone else's behalf.
"""

from __future__ import annotations

import hashlib
import secrets
import uuid
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Annotated

from fastapi import Depends, Request
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from loka.bounded_contexts.identity.infrastructure.persistence.models import RefreshTokenRow
from loka.interfaces.http.container import get_container
from loka.shared.domain.errors import AuthorizationDenied, Unauthenticated
from loka.shared.infrastructure.db.engine import Database

BEARER_SCHEME = "Bearer"
TOKEN_BYTES = 32
BEARER_CHALLENGE = 'Bearer realm="loka"'


@dataclass(frozen=True, slots=True)
class Principal:
    user_id: uuid.UUID
    is_admin: bool
    token_id: uuid.UUID


def hash_token(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()


def generate_token() -> str:
    return secrets.token_urlsafe(TOKEN_BYTES)


class BearerTokenAuthenticator:
    """Resolves a bearer token to a principal through the stored token hash."""

    def __init__(self, database: Database) -> None:
        # The database owns the session factory, which only exists once it is
        # connected; resolving it per call keeps construction order irrelevant.
        self._database = database

    @asynccontextmanager
    async def open_session(self) -> AsyncIterator[AsyncSession]:
        async with self._database.sessions() as session:
            yield session

    async def authenticate(self, session: AsyncSession, token: str) -> Principal | None:
        result = await session.execute(
            select(RefreshTokenRow).where(RefreshTokenRow.token_hash == hash_token(token))
        )
        record = result.scalar_one_or_none()
        if record is None or record.revoked_at is not None:
            return None
        if _as_utc(record.expires_at) <= datetime.now(UTC):
            return None
        return Principal(
            user_id=record.user_id,
            is_admin=record.is_admin_session,
            token_id=record.id,
        )


bearer_scheme = HTTPBearer(auto_error=False)


async def require_principal(request: Request) -> Principal:
    credentials: HTTPAuthorizationCredentials | None = await bearer_scheme(request)
    if credentials is None or credentials.scheme.lower() != BEARER_SCHEME.lower():
        raise Unauthenticated("a bearer token is required")
    authenticator = get_container(request).authenticator
    async with authenticator.open_session() as session:
        principal = await authenticator.authenticate(session, credentials.credentials)
    if principal is None:
        raise Unauthenticated("the bearer token is invalid or expired")
    return principal


async def require_admin(
    request: Request,
    principal: Annotated[Principal, Depends(require_principal)],
) -> Principal:
    """Route-guard for analyst endpoints: only admin sessions may call them."""
    if not principal.is_admin:
        raise AuthorizationDenied("analyst role required")
    return principal


def _as_utc(value: datetime) -> datetime:
    return value if value.tzinfo is not None else value.replace(tzinfo=UTC)
