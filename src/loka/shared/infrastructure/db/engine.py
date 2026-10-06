"""Async engine and session factory.

Everything is asyncpg. ``pool_pre_ping`` guards against connections killed by
the proxy, and ``statement_timeout`` guarantees we fail fast instead of
holding a worker forever.
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)

from loka.shared.infrastructure.config.settings import DatabaseSettings
from loka.shared.infrastructure.logging import get_logger

_logger = get_logger(__name__)


class Database:
    """Owns the engine lifecycle for one process."""

    def __init__(self, settings: DatabaseSettings, *, application_name: str = "loka") -> None:
        self._settings = settings
        self._engine: AsyncEngine | None = None
        self._sessions: async_sessionmaker[AsyncSession] | None = None
        self._application_name = application_name

    @property
    def engine(self) -> AsyncEngine:
        if self._engine is None:
            raise RuntimeError("Database.connect() has not been awaited")
        return self._engine

    @property
    def sessions(self) -> async_sessionmaker[AsyncSession]:
        if self._sessions is None:
            raise RuntimeError("Database.connect() has not been awaited")
        return self._sessions

    async def connect(self) -> None:
        if self._engine is not None:
            return
        dsn = self._settings.async_dsn
        self._engine = create_async_engine(
            dsn,
            pool_size=self._settings.pool_size,
            max_overflow=self._settings.max_overflow,
            pool_timeout=self._settings.pool_timeout,
            pool_recycle=self._settings.pool_recycle,
            pool_pre_ping=True,
            echo=False,
            connect_args={
                "server_settings": {
                    "application_name": self._application_name,
                    "statement_timeout": str(self._settings.statement_timeout_ms),
                    "timezone": "UTC",
                }
            },
        )
        self._sessions = async_sessionmaker(
            self._engine, expire_on_commit=False, autoflush=False
        )
        _logger.info("database_connected", dsn_host=self._settings.host)

    async def dispose(self) -> None:
        if self._engine is not None:
            await self._engine.dispose()
            _logger.info("database_disposed")
        self._engine = None
        self._sessions = None

    @asynccontextmanager
    async def session(self) -> AsyncIterator[AsyncSession]:
        session = self.sessions()
        try:
            yield session
            await session.commit()
        except Exception:
            await session.rollback()
            raise
        finally:
            await session.close()

    async def healthcheck(self) -> bool:
        from sqlalchemy import text

        try:
            async with self.engine.connect() as conn:
                await conn.execute(text("SELECT 1"))
            return True
        except Exception as exc:  # pragma: no cover - depends on infra
            _logger.warning("database_healthcheck_failed", error=str(exc))
            return False