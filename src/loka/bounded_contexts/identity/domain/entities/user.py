"""Identity context: users, roles and phone ownership.

A phone number is the primary identity anchor because WhatsApp is the
product interface. Exactly one active account per E.164 number.
"""

from __future__ import annotations

import uuid
from datetime import datetime, timedelta
from enum import StrEnum

from loka.bounded_contexts.identity.domain.value_objects.phone_number import PhoneNumber
from loka.shared.domain.aggregate import AggregateRoot
from loka.shared.domain.clock import ensure_utc
from loka.shared.domain.errors import AuthorizationDenied, InvalidStateTransition


class Role(StrEnum):
    TENANT = "TENANT"
    LANDLORD = "LANDLORD"
    ADMIN = "ADMIN"
    MODERATOR = "MODERATOR"
    FRAUD_ANALYST = "FRAUD_ANALYST"


class AccountStatus(StrEnum):
    PENDING_PHONE_VERIFICATION = "PENDING_PHONE_VERIFICATION"
    ACTIVE = "ACTIVE"
    SUSPENDED = "SUSPENDED"
    CLOSED = "CLOSED"


class VerificationPurpose(StrEnum):
    REGISTRATION = "REGISTRATION"
    SENSITIVE_ACTION = "SENSITIVE_ACTION"
    LOGIN = "LOGIN"


CODE_TTL_MINUTES = 5
CODE_LENGTH = 6
MAX_SEND_PER_HOUR = 5


class User(AggregateRoot):
    __slots__ = (
        "created_at",
        "display_name",
        "last_seen_at",
        "phone",
        "phone_verified_at",
        "roles",
        "status",
        "suspension_reason",
        "updated_at",
        "user_id",
    )

    def __init__(
        self,
        *,
        user_id: uuid.UUID,
        phone: PhoneNumber,
        display_name: str | None,
        now: datetime,
        roles: frozenset[Role] | None = None,
    ) -> None:
        super().__init__(aggregate_type="User")
        self._assign_id(user_id)
        self.user_id = user_id
        self.phone = phone
        self.roles = roles or frozenset({Role.TENANT})
        self.status = AccountStatus.PENDING_PHONE_VERIFICATION
        self.display_name = (display_name or "").strip() or None
        self.phone_verified_at: datetime | None = None
        self.last_seen_at: datetime | None = None
        self.created_at = ensure_utc(now)
        self.updated_at = ensure_utc(now)
        self.suspension_reason: str | None = None

    @property
    def is_active(self) -> bool:
        return self.status is AccountStatus.ACTIVE

    def has_role(self, role: Role) -> bool:
        return role in self.roles

    def require_role(self, *roles: Role) -> None:
        if self.status is AccountStatus.SUSPENDED:
            raise AuthorizationDenied(
                "account is suspended", context={"reason": self.suspension_reason}
            )
        if self.status is AccountStatus.CLOSED:
            raise AuthorizationDenied("account is closed")
        if not self.roles.intersection(roles):
            raise AuthorizationDenied(
                "role required",
                context={
                    "required": [role.value for role in roles],
                    "actual": self._role_names,
                },
            )

    @property
    def _role_names(self) -> list[str]:
        return sorted(role.value for role in self.roles)

    def confirm_phone(self, *, now: datetime) -> None:
        if self.status is AccountStatus.CLOSED:
            self._reject("cannot verify a closed account")
        if self.phone_verified_at is None:
            self.phone_verified_at = ensure_utc(now)
        if self.status is AccountStatus.PENDING_PHONE_VERIFICATION:
            self.status = AccountStatus.ACTIVE
        self.updated_at = ensure_utc(now)
        self._bump()
        self.record(
            "UserPhoneVerified",
            occurred_at=now,
            actor_id=self.id,
            payload={"user_id": str(self.id), "phone": self.phone.masked()},
        )

    def grant_role(self, role: Role, *, now: datetime, actor_id: uuid.UUID | None = None) -> None:
        if role is Role.LANDLORD and self.status is not AccountStatus.ACTIVE:
            self._reject("phone must be verified before becoming a landlord")
        if role in self.roles:
            return
        self.roles = self.roles | {role}
        self.updated_at = ensure_utc(now)
        self._bump()
        self.record(
            "UserRoleGranted",
            occurred_at=now,
            actor_id=actor_id,
            payload={"user_id": str(self.id), "role": role.value},
        )

    def revoke_role(self, role: Role, *, now: datetime, actor_id: uuid.UUID | None = None) -> None:
        if role not in self.roles:
            return
        if self.roles == {role}:
            self._reject("an account must keep at least one role")
        self.roles = self.roles - {role}
        self.updated_at = ensure_utc(now)
        self._bump()
        self.record(
            "UserRoleRevoked",
            occurred_at=now,
            actor_id=actor_id,
            payload={"user_id": str(self.id), "role": role.value},
        )

    def rename(self, display_name: str, *, now: datetime) -> None:
        cleaned = display_name.strip()
        if not 2 <= len(cleaned) <= 80:
            self._reject("display name must be 2 to 80 characters", length=len(cleaned))
        self.display_name = cleaned
        self.updated_at = ensure_utc(now)
        self._bump()

    def touch(self, *, now: datetime) -> None:
        self.last_seen_at = ensure_utc(now)

    def suspend(self, *, now: datetime, reason: str, actor_id: uuid.UUID | None = None) -> None:
        if self.status is AccountStatus.SUSPENDED:
            raise InvalidStateTransition("account is already suspended")
        if self.status is AccountStatus.CLOSED:
            raise InvalidStateTransition("cannot suspend a closed account")
        self.status = AccountStatus.SUSPENDED
        self.suspension_reason = reason
        self.updated_at = ensure_utc(now)
        self._bump()
        self.record(
            "UserSuspended",
            occurred_at=now,
            actor_id=actor_id,
            payload={"user_id": str(self.id), "reason": reason},
        )

    def reinstate(self, *, now: datetime, actor_id: uuid.UUID | None = None) -> None:
        if self.status is not AccountStatus.SUSPENDED:
            raise InvalidStateTransition("account is not suspended")
        self.status = (
            AccountStatus.ACTIVE
            if self.phone_verified_at
            else AccountStatus.PENDING_PHONE_VERIFICATION
        )
        self.suspension_reason = None
        self.updated_at = ensure_utc(now)
        self._bump()
        self.record(
            "UserReinstated",
            occurred_at=now,
            actor_id=actor_id,
            payload={"user_id": str(self.id)},
        )

    def close(self, *, now: datetime) -> None:
        if self.status is AccountStatus.CLOSED:
            return
        self.status = AccountStatus.CLOSED
        self.updated_at = ensure_utc(now)
        self._bump()
        self.record("UserClosed", occurred_at=now, payload={"user_id": str(self.id)})


class PhoneVerificationChallenge:
    """Short-lived OTP challenge.

    Kept as a separate aggregate: challenges are high-volume, disposable and
    never share a lifecycle with the account itself.
    """

    __slots__ = (
        "attempts",
        "challenge_id",
        "code_hash",
        "consumed_at",
        "created_at",
        "expires_at",
        "phone",
        "purpose",
        "user_id",
    )

    def __init__(
        self,
        *,
        challenge_id: uuid.UUID,
        user_id: uuid.UUID,
        phone: PhoneNumber,
        purpose: VerificationPurpose,
        code_hash: str,
        now: datetime,
    ) -> None:
        self.challenge_id = challenge_id
        self.user_id = user_id
        self.phone = phone
        self.purpose = purpose
        self.code_hash = code_hash
        self.created_at = ensure_utc(now)
        self.expires_at = ensure_utc(now) + timedelta(minutes=CODE_TTL_MINUTES)
        self.attempts = 0
        self.consumed_at: datetime | None = None

    @property
    def id(self) -> uuid.UUID:
        return self.challenge_id

    def is_usable(self, now: datetime) -> bool:
        return self.consumed_at is None and ensure_utc(now) < self.expires_at

    def verify(self, submitted_hash: str, *, now: datetime) -> bool:
        if not self.is_usable(now):
            return False
        if self.attempts >= 3:
            return False
        if not _constant_time_equals(submitted_hash, self.code_hash):
            self.attempts += 1
            return False
        self.consumed_at = ensure_utc(now)
        return True


def _constant_time_equals(left: str, right: str) -> bool:
    import hmac

    return hmac.compare_digest(left, right)