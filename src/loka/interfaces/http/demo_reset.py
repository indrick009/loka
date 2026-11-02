"""DEMO ONLY — the ``_empty_all_data`` WhatsApp command wipes every piece of data.

Sent by any number, it empties the database (all tables but the migration
version), Redis and the media bucket, then confirms to the sender. It exists so
a demo can start again from nothing, and it is off unless
``DEMO_RESET_ENABLED=true``.

To remove it: delete this file and the ``demo_reset`` lines in
``routers/whatsapp.py`` and ``settings.py``.
"""

from __future__ import annotations

import asyncio

import boto3
import httpx
from botocore.config import Config
from sqlalchemy import text

from loka.interfaces.http.container import Container
from loka.shared.infrastructure.logging import get_logger

COMMAND = "_empty_all_data"

_logger = get_logger("demo_reset")


def is_demo_reset(container: Container, message: str | None) -> bool:
    return container.settings.demo_reset_enabled and (message or "").strip() == COMMAND


async def reset_everything(container: Container, phone: str) -> None:
    await _empty_database(container)
    if container.redis is not None:
        await container.redis.flushdb()
    await asyncio.to_thread(_empty_bucket, container)
    _logger.warning("demo_reset_done")
    await _confirm(container, phone)


async def _empty_database(container: Container) -> None:
    async with container.database.session() as session:
        tables = (
            await session.execute(
                text(
                    "SELECT tablename FROM pg_tables WHERE schemaname = 'public' "
                    "AND tablename <> 'alembic_version'"
                )
            )
        ).scalars().all()
        if tables:
            names = ", ".join(f'"{name}"' for name in tables)
            await session.execute(text(f"TRUNCATE {names} RESTART IDENTITY CASCADE"))


def _empty_bucket(container: Container) -> None:
    storage = container.settings.object_storage
    client = boto3.client(
        "s3",
        endpoint_url=storage.endpoint,
        aws_access_key_id=storage.access_key.get_secret_value(),
        aws_secret_access_key=storage.secret_key.get_secret_value(),
        region_name=storage.region,
        config=Config(signature_version="s3v4"),
    )
    for page in client.get_paginator("list_objects_v2").paginate(Bucket=storage.bucket):
        keys = [{"Key": item["Key"]} for item in page.get("Contents", [])]
        if keys:
            client.delete_objects(Bucket=storage.bucket, Delete={"Objects": keys})


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
                json={"to": phone, "text": "Démo : toutes les données ont été vidées."},
            )
        except httpx.HTTPError as exc:
            _logger.warning("demo_reset_confirm_failed", error=type(exc).__name__)
