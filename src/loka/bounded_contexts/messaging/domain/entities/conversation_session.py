"""Conversation session aggregate.

The conversational state machine lives in the database, never inside an LLM
prompt. A conversation must resume correctly after a worker crash, a deploy
or a reconnect, which is only possible if the current step is durable and
version-checked.
"""

from __future__ import annotations

import uuid
from datetime import datetime, timedelta
from enum import StrEnum

from loka.bounded_contexts.identity.domain.value_objects.phone_number import PhoneNumber
from loka.shared.domain.aggregate import AggregateRoot
from loka.shared.domain.clock import ensure_utc
from loka.shared.domain.errors import InvalidStateTransition

SESSION_IDLE_TIMEOUT = timedelta(minutes=30)


class FlowName(StrEnum):
    LANDLORD_ONBOARDING = "LANDLORD_ONBOARDING"
    PROPERTY_CREATION = "PROPERTY_CREATION"
    PROPERTY_QUALITY_REMEDIATION = "PROPERTY_QUALITY_REMEDIATION"
    AVAILABILITY_CHECK = "AVAILABILITY_CHECK"
    TENANT_ONBOARDING = "TENANT_ONBOARDING"
    PROPERTY_SEARCH = "PROPERTY_SEARCH"
    APPLICATION_FLOW = "APPLICATION_FLOW"
    VISIT_SCHEDULING = "VISIT_SCHEDULING"
    VISIT_FEEDBACK = "VISIT_FEEDBACK"
    RENTAL_FEEDBACK = "RENTAL_FEEDBACK"
    REPORT_FLOW = "REPORT_FLOW"
    SUPPORT = "SUPPORT"


class FlowStep(StrEnum):
    START = "START"
    COLLECT_PHONE = "COLLECT_PHONE"
    COLLECT_IDENTITY = "COLLECT_IDENTITY"
    COLLECT_CNI = "COLLECT_CNI"
    COLLECT_SELFIE = "COLLECT_SELFIE"
    VERIFYING = "VERIFYING"
    VERIFIED = "VERIFIED"
    CREATE_PROPERTY = "CREATE_PROPERTY"
    COLLECT_PROPERTY_TYPE = "COLLECT_PROPERTY_TYPE"
    COLLECT_LOCATION = "COLLECT_LOCATION"
    COLLECT_PRICE = "COLLECT_PRICE"
    COLLECT_FEATURES = "COLLECT_FEATURES"
    COLLECT_STANDING = "COLLECT_STANDING"
    COLLECT_MEDIA = "COLLECT_MEDIA"
    CONFIRM_PROPERTY = "CONFIRM_PROPERTY"
    PUBLISHED = "PUBLISHED"
    COLLECT_CHARGES = "COLLECT_CHARGES"
    COLLECT_MINIMUM_DURATION = "COLLECT_MINIMUM_DURATION"
    COLLECT_AVAILABILITY = "COLLECT_AVAILABILITY"
    COLLECT_CONDITIONS = "COLLECT_CONDITIONS"
    COLLECT_SEARCH_CRITERIA = "COLLECT_SEARCH_CRITERIA"
    PRESENT_RESULTS = "PRESENT_RESULTS"
    COLLECT_PROPERTY_CHOICE = "COLLECT_PROPERTY_CHOICE"
    COLLECT_VISIT_SLOT = "COLLECT_VISIT_SLOT"
    CONFIRM_VISIT = "CONFIRM_VISIT"
    COLLECT_VISIT_FEEDBACK = "COLLECT_VISIT_FEEDBACK"
    COLLECT_ACQUISITION_SOURCE = "COLLECT_ACQUISITION_SOURCE"
    COLLECT_REPORT_REASON = "COLLECT_REPORT_REASON"
    AWAITING_CONFIRMATION = "AWAITING_CONFIRMATION"
    DONE = "DONE"


class SessionStatus(StrEnum):
    ACTIVE = "ACTIVE"
    IDLE = "IDLE"
    COMPLETED = "COMPLETED"
    ABANDONED = "ABANDONED"


class ConversationSession(AggregateRoot):
    __slots__ = (
        "assigned_property_id",
        "context",
        "flow",
        "language",
        "last_step_at",
        "phone",
        "revision",
        "session_id",
        "started_at",
        "status",
        "step",
        "updated_at",
        "user_id",
    )

    def __init__(
        self,
        *,
        session_id: uuid.UUID,
        user_id: uuid.UUID | None,
        phone: PhoneNumber,
        now: datetime,
        flow: FlowName = FlowName.SUPPORT,
    ) -> None:
        super().__init__(aggregate_type="ConversationSession")
        self._assign_id(session_id)
        self.session_id = session_id
        self.user_id = user_id
        self.phone = phone
        self.flow = flow
        self.step = FlowStep.START
        self.status = SessionStatus.ACTIVE
        self.context: dict[str, object] = {}
        self.language = "fr"
        self.last_step_at = ensure_utc(now)
        self.started_at = ensure_utc(now)
        self.updated_at = ensure_utc(now)
        self.revision = 0
        self.assigned_property_id: uuid.UUID | None = None

    @property
    def is_active(self) -> bool:
        return self.status is SessionStatus.ACTIVE

    def start_flow(self, flow: FlowName, *, now: datetime, first_step: FlowStep) -> None:
        if self.is_idle(now) and self.status is SessionStatus.ACTIVE:
            self.status = SessionStatus.IDLE
        self.flow = flow
        self.step = first_step
        self.status = SessionStatus.ACTIVE
        self.context = {}
        self._touch(now)
        self.record(
            "ConversationFlowStarted",
            occurred_at=now,
            payload={
                "session_id": str(self.id),
                "flow": flow.value,
                "step": first_step.value,
            },
        )

    def transition(
        self,
        to_step: FlowStep,
        *,
        now: datetime,
        expected_revision: int | None = None,
        context_patch: dict[str, object] | None = None,
    ) -> None:
        """Advance the state machine under optimistic concurrency control.

        ``expected_revision`` guards against two workers advancing the same
        session after a redelivery race.
        """
        if expected_revision is not None and expected_revision != self.revision:
            raise InvalidStateTransition(
                "conversation session was modified concurrently",
                context={
                    "session_id": str(self.id),
                    "expected_revision": expected_revision,
                    "actual_revision": self.revision,
                },
            )
        if self.status in (SessionStatus.COMPLETED, SessionStatus.ABANDONED):
            raise InvalidStateTransition(
                f"cannot advance a {self.status.value} session",
                context={"session_id": str(self.id), "flow": self.flow.value},
            )
        previous = self.step
        self.step = to_step
        if context_patch:
            self.context.update(context_patch)
        self.status = (
            SessionStatus.COMPLETED if to_step is FlowStep.DONE else SessionStatus.ACTIVE
        )
        self._touch(now)
        self.record(
            "ConversationStepAdvanced",
            occurred_at=now,
            payload={
                "session_id": str(self.id),
                "flow": self.flow.value,
                "from_step": previous.value,
                "to_step": to_step.value,
                "revision": self.revision,
            },
        )

    def merge_context(self, patch: dict[str, object], *, now: datetime) -> None:
        """Persist facts extracted from a message so a crash loses nothing."""
        self.context.update(patch)
        self._touch(now)

    def attach_property(self, property_id: uuid.UUID, *, now: datetime) -> None:
        self.assigned_property_id = property_id
        self._touch(now)

    def is_idle(self, now: datetime) -> bool:
        return ensure_utc(now) - self.last_step_at > SESSION_IDLE_TIMEOUT

    def abort(self, *, reason: str, now: datetime) -> None:
        if self.status is SessionStatus.ABANDONED:
            return
        self.status = SessionStatus.ABANDONED
        self.context["abort_reason"] = reason
        self._touch(now)
        self.record(
            "ConversationAbandoned",
            occurred_at=now,
            payload={"session_id": str(self.id), "reason": reason},
        )

    def restart(self, *, now: datetime) -> None:
        self.flow = FlowName.SUPPORT
        self.step = FlowStep.START
        self.status = SessionStatus.ACTIVE
        self.context = {}
        self.assigned_property_id = None
        self._touch(now)

    def _touch(self, now: datetime) -> None:
        self.revision += 1
        self.last_step_at = ensure_utc(now)
        self.updated_at = ensure_utc(now)
        self._bump()