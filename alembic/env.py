"""Alembic environment, wired to the async engine.

Migrations run with ``alembic upgrade head`` inside the compose service and
from the integration test fixtures, so the same code path is used in CI, in
tests and in production.
"""

from __future__ import annotations

import asyncio
from concurrent.futures import ThreadPoolExecutor
from logging.config import fileConfig

from alembic import context
from sqlalchemy import pool
from sqlalchemy.ext.asyncio import async_engine_from_config

from loka.shared.infrastructure.config.settings import get_settings
from loka.shared.infrastructure.db import registry as _registry  # noqa: F401
from loka.shared.infrastructure.db.base import Base

config = context.config

if config.config_file_name is not None:
    fileConfig(config.config_file_name)

target_metadata = Base.metadata


def _database_url() -> str:
    """An explicitly configured URL always wins over the environment.

    ``alembic.ini`` ships without ``sqlalchemy.url`` so a stale placeholder can
    never win over the environment.
    """
    configured = config.get_main_option("sqlalchemy.url")
    if configured and "://" in configured and not configured.startswith("driver://"):
        return configured
    settings = get_settings()
    config.set_main_option("sqlalchemy.url", settings.database.async_dsn)
    return settings.database.async_dsn


def run_migrations_offline() -> None:
    settings = get_settings()
    context.configure(
        url=settings.database.sync_dsn,
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramestyle": "named"},
        compare_type=True,
        compare_server_default=True,
    )
    with context.begin_transaction():
        context.run_migrations()


def _do_run_migrations(connection: object) -> None:
    context.configure(
        connection=connection,  # type: ignore[arg-type]
        target_metadata=target_metadata,
        compare_type=True,
        compare_server_default=True,
    )
    with context.begin_transaction():
        context.run_migrations()


async def run_migrations_online() -> None:
    connectable = async_engine_from_config(
        config.get_section(config.config_ini_section, {}),
        prefix="sqlalchemy.",
        poolclass=pool.NullPool,
    )
    async with connectable.connect() as connection:
        await connection.run_sync(_do_run_migrations)
    await connectable.dispose()


def _run() -> None:
    try:
        asyncio.get_running_loop()
    except RuntimeError:
        asyncio.run(run_migrations_online())
        return
    # Already inside an event loop (integration tests): the migrations need
    # their own loop, so they get a thread of their own.
    with ThreadPoolExecutor(max_workers=1) as pool_executor:
        pool_executor.submit(asyncio.run, run_migrations_online()).result()


if context.is_offline_mode():
    run_migrations_offline()
else:
    _database_url()
    _run()
