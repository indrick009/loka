"""Visit domain rules.

The aggregate is where the visit workflow invariants live: schedule in the
future, complete only a scheduled visit, and a terminal visit that cannot move
again. These tests pin those rules down without any infrastructure.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime

import pytest

from loka.bounded_contexts.visit.domain.entities.visit import Visit, VisitStatus
from loka.shared.domain.errors import InvalidStateTransition, InvariantViolation

NOW = datetime(2026, 3, 1, 9, 0, tzinfo=UTC)
LANDLORD_ID = uuid.uuid4()
TENANT_ID = uuid.uuid4()


def _visit() -> Visit:
    return Visit(
        visit_id=uuid.uuid4(),
        property_id=uuid.uuid4(),
        landlord_id=LANDLORD_ID,
        tenant_id=TENANT_ID,
        preferred_date=NOW.date(),
        message="Je souhaite visiter",
        now=NOW,
    )


def test_requesting_a_visit_records_an_event() -> None:
    visit = _visit()
    assert visit.status is VisitStatus.REQUESTED
    assert visit.is_open
    assert [e.event_type for e in visit.pull_events()] == ["VisitRequested"]


def test_scheduling_transitions_to_scheduled() -> None:
    visit = _visit()
    visit.pull_events()
    when = NOW.replace(hour=15)
    visit.schedule(when=when, now=NOW)
    assert visit.status is VisitStatus.SCHEDULED
    assert visit.scheduled_for == when
    assert [e.event_type for e in visit.pull_events()] == ["VisitScheduled"]


def test_rescheduling_records_a_rescheduled_event() -> None:
    visit = _visit()
    visit.schedule(when=NOW.replace(hour=15), now=NOW)
    visit.pull_events()
    visit.schedule(when=NOW.replace(hour=17), now=NOW)
    assert [e.event_type for e in visit.pull_events()] == ["VisitRescheduled"]


def test_scheduling_in_the_past_is_rejected() -> None:
    visit = _visit()
    with pytest.raises(InvariantViolation):
        visit.schedule(when=NOW.replace(hour=8), now=NOW)


def test_only_a_scheduled_visit_can_be_completed() -> None:
    visit = _visit()
    with pytest.raises(InvalidStateTransition):
        visit.complete(now=NOW)
    visit.schedule(when=NOW.replace(hour=15), now=NOW)
    visit.complete(now=NOW.replace(hour=16))
    assert visit.status is VisitStatus.COMPLETED
    assert "VisitCompleted" in [e.event_type for e in visit.pull_events()]


def test_no_show_is_only_possible_after_scheduling() -> None:
    visit = _visit()
    with pytest.raises(InvalidStateTransition):
        visit.mark_no_show(now=NOW)
    visit.schedule(when=NOW.replace(hour=15), now=NOW)
    visit.mark_no_show(now=NOW.replace(hour=16))
    assert visit.status is VisitStatus.NO_SHOW


def test_a_terminal_visit_cannot_be_cancelled() -> None:
    visit = _visit()
    visit.cancel(reason="tenant unavailable", now=NOW)
    assert visit.status is VisitStatus.CANCELLED
    with pytest.raises(InvalidStateTransition):
        visit.cancel(now=NOW)