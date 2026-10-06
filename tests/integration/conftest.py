"""Integration test fixtures backed by real infrastructure.

The schema comes from the Alembic migrations, not from ``metadata.create_all``,
so the tests exercise what production will actually run. Isolation is done with
``TRUNCATE`` between tests: it is much faster than dropping tables and it keeps
``alembic_version`` (and therefore drift detection) intact.
"""

from __future__ import annotations

import asyncio
import uuid
from collections.abc import AsyncIterator, Iterator
from pathlib import Path
from urllib.parse import urlparse, urlunparse

import pytest
import pytest_asyncio
from pydantic import SecretStr
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from loka.shared.domain.aggregate import AggregateRoot
from loka.shared.infrastructure.config.settings import Settings
from loka.shared.infrastructure.db.base import Base
from loka.shared.infrastructure.db.engine import Database
from loka.shared.infrastructure.db.registry import *  # noqa: F403
from loka.shared.infrastructure.db.unit_of_work import SqlAlchemyUnitOfWork
from loka.shared.infrastructure.redis_client import build_redis


@pytest.fixture(scope="session")
def integration_settings() -> Settings:
    """Connection targets come from the environment.

    Inside ``docker compose run test`` that is the compose network (``postgres``,
    ``redis``); on the host it is ``localhost``. Only the database name, the
    Redis DB index and the encryption key are forced, so the suite can never
    truncate the development database.
    """
    base = Settings()
    return base.model_copy(
        update={
            "environment": "test",
            "database": base.database.model_copy(
                update={"database": "loka_test", "pool_size": 5, "max_overflow": 2}
            ),
            "redis_url": _redis_db_url(base.redis_url, db=1),
            "encryption_key": SecretStr("integration-test-key-material-32-bytes!!"),
        }
    )


def _redis_db_url(url: str, db: int) -> str:
    parsed = urlparse(url)
    return urlunparse(parsed._replace(path=f"/{db}"))


async def _truncate_everything(db: Database) -> None:
    """Empty every application table without touching alembic_version."""
    tables = ", ".join(f'"{table.name}"' for table in reversed(Base.metadata.sorted_tables))
    if not tables:
        return
    async with db.engine.begin() as conn:
        await conn.execute(text(f"TRUNCATE {tables} RESTART IDENTITY CASCADE"))


async def _prepare_schema(settings: Settings) -> None:
    """Rebuild the schema from the migrations once per session.

    ``Base.metadata.drop_all`` leaves ``alembic_version`` untouched, so it has to
    be cleared as well or Alembic would consider the database up to date and
    skip the very migrations the tests are meant to exercise.
    """
    db = Database(settings.database, application_name="loka-test-migrate")
    await db.connect()
    try:
        async with db.engine.begin() as conn:
            await conn.run_sync(Base.metadata.drop_all)
            await conn.execute(text("DROP TABLE IF EXISTS alembic_version"))
        _run_migrations(settings)
    finally:
        await db.dispose()


def _run_migrations(settings: Settings) -> None:
    from alembic import command
    from alembic.config import Config

    root = Path(__file__).resolve().parents[2]
    config = Config(str(root / "alembic.ini"))
    config.set_main_option("script_location", str(root / "alembic"))
    config.set_main_option("sqlalchemy.url", settings.database.async_dsn.replace("%", "%%"))
    command.upgrade(config, "head")


@pytest.fixture(scope="session")
def migrated_schema(integration_settings: Settings) -> Iterator[None]:
    """Session-scoped schema, built by the real migration chain."""
    asyncio.run(_prepare_schema(integration_settings))
    yield


@pytest_asyncio.fixture
async def database(
    integration_settings: Settings, migrated_schema: None
) -> AsyncIterator[Database]:
    db = Database(integration_settings.database, application_name="loka-test")
    await db.connect()
    await _truncate_everything(db)
    yield db
    await _truncate_everything(db)
    await db.dispose()


@pytest_asyncio.fixture
async def session(database: Database) -> AsyncIterator[AsyncSession]:
    async with database.session() as active:
        yield active


@pytest_asyncio.fixture
async def uow(database: Database) -> AsyncIterator[SqlAlchemyUnitOfWork]:
    async with SqlAlchemyUnitOfWork(database.sessions) as unit:
        yield unit


@pytest_asyncio.fixture
async def redis(integration_settings: Settings):  # type: ignore[no-untyped-def]
    client = await build_redis(integration_settings.redis_url, max_connections=10)
    await client.flushdb()
    yield client
    await client.flushdb()
    await client.aclose()


class DummyAggregate(AggregateRoot):
    __slots__ = ("value",)

    def __init__(self, aggregate_id: uuid.UUID, now) -> None:  # type: ignore[no-untyped-def]
        super().__init__(aggregate_type="Dummy")
        self._assign_id(aggregate_id)
        self.value = 0
        self.record("DummyCreated", occurred_at=now, payload={"id": str(aggregate_id)})


@pytest.fixture
def dummy_factory() -> object:
    return DummyAggregate
