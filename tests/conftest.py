"""Test bootstrap and shared fixtures."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime

import pytest

from loka.shared.domain.clock import FrozenClock
from loka.shared.domain.identifiers import Uuid7Generator
from loka.shared.infrastructure.config.settings import Settings
from loka.shared.infrastructure.logging import configure_logging


@pytest.fixture(scope="session", autouse=True)
def _logging() -> None:
    configure_logging(level="WARNING", json_output=False)


@pytest.fixture
def now() -> datetime:
    return datetime(2026, 3, 1, 9, 0, tzinfo=UTC)


@pytest.fixture
def clock(now: datetime) -> FrozenClock:
    return FrozenClock(now)


@pytest.fixture
def ids() -> Uuid7Generator:
    return Uuid7Generator()


@pytest.fixture
def new_id() -> uuid.UUID:
    return uuid.uuid4()


@pytest.fixture
def settings() -> Settings:
    return Settings(
        environment="test",
        encryption_key="test-key-material-for-field-encryption",
    )