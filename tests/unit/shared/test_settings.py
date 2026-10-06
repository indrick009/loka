"""Settings must actually be configurable through the environment.

The compose file ships a ``.env``; if a name drifts from ``Settings`` the
process silently keeps a default and talks to the wrong database, so this is
worth locking down.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from loka.shared.infrastructure.config.settings import Settings

ROOT = Path(__file__).resolve().parents[3]
ENV_EXAMPLE = ROOT / ".env.example"


def _example_pairs() -> list[tuple[str, str]]:
    pairs: list[tuple[str, str]] = []
    for raw in ENV_EXAMPLE.read_text().splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        name, _, value = line.partition("=")
        pairs.append((name.strip(), value.strip()))
    return pairs


def test_env_example_is_not_empty() -> None:
    assert ENV_EXAMPLE.exists()
    assert len(_example_pairs()) > 20


def test_every_env_example_name_maps_to_a_real_setting(tmp_path: Path) -> None:
    settings = Settings(_env_file=None)
    env_file = tmp_path / ".env"
    env_file.write_text("\n".join(f"{name}={value}" for name, value in _example_pairs()))
    resolved = Settings(_env_file=env_file)
    assert resolved.database.host == "postgres"
    assert resolved.database.database == "loka"
    assert resolved.broker.url.get_secret_value().startswith("amqp://")
    assert resolved.redis_url.startswith("redis://")
    assert resolved.whatsapp.gateway_url.startswith("http")
    assert resolved.ai.provider == "openrouter"
    assert resolved.payment.service_fee_xaf == 1000
    assert resolved.fraud.max_active_properties_per_landlord == 25
    assert resolved.observability.log_level == "INFO"
    assert settings.database.host == "localhost"


def test_nested_and_flat_names_are_rejected_for_nested_sections(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A flat POSTGRES_HOST must not silently override the database host."""
    monkeypatch.setenv("POSTGRES_HOST", "somewhere-else")
    monkeypatch.setenv("DATABASE__HOST", "explicit")
    assert Settings(_env_file=None).database.host == "explicit"


def test_secrets_are_not_exposed_in_repr() -> None:
    rendered = repr(Settings(_env_file=None))
    assert "loka:loka@" not in rendered
    assert "**********" in rendered


def test_is_production_flag() -> None:
    assert Settings(_env_file=None, environment="development").is_production is False
    assert Settings(_env_file=None, environment="production").is_production is True


def test_async_and_sync_dsn_point_at_the_same_database() -> None:
    settings = Settings(_env_file=None, database={"host": "db", "database": "loka_test"})
    assert settings.database.async_dsn.endswith("/loka_test")
    assert settings.database.sync_dsn.endswith("/loka_test")
    assert settings.database.async_dsn.startswith("postgresql+asyncpg://")
