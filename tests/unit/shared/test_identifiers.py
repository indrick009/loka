"""Identifier generation must be unique, sortable and RFC 9562 shaped."""

from __future__ import annotations

import time
import uuid

import pytest

import loka.shared.domain.identifiers as identifiers
from loka.shared.domain.identifiers import IdGenerator, SequenceIdGenerator, Uuid7Generator


def test_generator_satisfies_the_port() -> None:
    assert isinstance(Uuid7Generator(), IdGenerator)
    assert isinstance(SequenceIdGenerator(), IdGenerator)


def test_uuid7_uses_the_rfc_9562_layout() -> None:
    generated = Uuid7Generator().new_id()
    assert generated.version == 7
    assert (generated.int >> 62) & 0b11 == 0b10
    assert abs((generated.int >> 80) - int(time.time() * 1000)) < 2_000


def test_uuid7_ids_are_unique_and_monotonic() -> None:
    generator = Uuid7Generator()
    generated = [generator.new_id() for _ in range(50_000)]
    assert len(set(generated)) == len(generated)
    assert generated == sorted(generated)


def test_uuid7_stays_ordered_when_many_ids_share_one_millisecond(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    frozen = 1_791_253_906.0
    generator = Uuid7Generator()
    generator._last_ms = int(frozen) - 5_000
    monkeypatch.setattr(identifiers.time, "time", lambda: frozen)

    burst = [generator.new_id() for _ in range(12_000)]

    assert len(set(burst)) == len(burst)
    assert burst == sorted(burst)
    assert burst[0].version == 7


def test_uuid7_survives_a_clock_that_jumps_forwards_and_backwards(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls = {"n": 0}

    def wild_clock() -> float:
        calls["n"] += 1
        step = calls["n"]
        return 1_791_253_906.0 + (step % 7) * 0.5 - (step % 3) * 1.7

    monkeypatch.setattr(identifiers.time, "time", wild_clock)
    generator = Uuid7Generator()
    generated = [generator.new_id() for _ in range(2_000)]

    assert len(set(generated)) == len(generated)
    assert generated == sorted(generated)


def test_uuid7_never_reuses_a_timestamp_after_a_clock_step_back(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    clock = {"now": 1_791_253_906.0}
    monkeypatch.setattr(identifiers.time, "time", lambda: clock["now"])
    generator = Uuid7Generator()

    before = [generator.new_id() for _ in range(5)]
    clock["now"] -= 60.0
    after = [generator.new_id() for _ in range(5)]

    assert before + after == sorted(before + after)
    assert len(set(before + after)) == 10


def test_module_level_generator_is_shared() -> None:
    first = identifiers.new_id()
    second = identifiers.new_id()
    assert first < second
    assert isinstance(second, uuid.UUID)
