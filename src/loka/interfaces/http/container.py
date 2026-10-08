"""Typed application container.

The controllers need the database, the authenticator and the settings; passing
them as untyped dict entries would push ``cast`` calls into every route.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, cast

from fastapi import Request
from redis.asyncio import Redis

from loka.shared.infrastructure.broker.topology import Broker
from loka.shared.infrastructure.config.settings import Settings
from loka.shared.infrastructure.db.engine import Database

if TYPE_CHECKING:
    from loka.interfaces.http.auth import BearerTokenAuthenticator


@dataclass
class Container:
    settings: Settings
    database: Database
    authenticator: BearerTokenAuthenticator
    redis: Redis | None = None
    # Installed during startup. The endpoint that uses it reconnects lazily, so
    # a broker that was unreachable when the API booted degrades that one
    # request to a 503 the transport can retry instead of taking the process
    # down with it.
    broker: Broker | None = None


def get_container(request: Request) -> Container:
    return cast(Container, request.app.state.container)
