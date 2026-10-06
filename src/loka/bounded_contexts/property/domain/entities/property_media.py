"""Property media item.

Photos and videos live in object storage; this record holds only the
reference, the checksum and the perceptual hash used later for duplicate
detection.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from datetime import datetime
from enum import StrEnum

from loka.shared.domain.clock import ensure_utc
from loka.shared.domain.errors import InvariantViolation


class MediaKind(StrEnum):
    PHOTO = "PHOTO"
    VIDEO = "VIDEO"
    FLOOR_PLAN = "FLOOR_PLAN"
    DOCUMENT = "DOCUMENT"


class MediaStatus(StrEnum):
    PENDING_UPLOAD = "PENDING_UPLOAD"
    UPLOADED = "UPLOADED"
    QUARANTINED = "QUARANTINED"
    REJECTED = "REJECTED"


@dataclass(slots=True)
class PropertyMedia:
    media_id: uuid.UUID
    object_key: str
    kind: MediaKind
    status: MediaStatus = MediaStatus.PENDING_UPLOAD
    checksum: str | None = None
    position: int = 0
    width: int | None = None
    height: int | None = None
    perceptual_hash: str | None = None
    uploaded_at: datetime | None = None
    quarantine_reason: str | None = None
    created_at: datetime = field(default_factory=lambda: ensure_utc(datetime.now()))

    @property
    def id(self) -> uuid.UUID:
        return self.media_id

    def mark_uploaded(
        self, *, perceptual_hash: str | None = None, when: datetime | None = None
    ) -> None:
        if self.status is MediaStatus.REJECTED:
            raise InvariantViolation("PropertyMedia: rejected media cannot be uploaded")
        self.status = MediaStatus.UPLOADED
        self.uploaded_at = ensure_utc(when or datetime.now())
        if perceptual_hash:
            self.perceptual_hash = perceptual_hash

    def quarantine(self, *, reason: str) -> None:
        self.status = MediaStatus.QUARANTINED
        self.quarantine_reason = reason

    def approve(self) -> None:
        if self.status is MediaStatus.REJECTED:
            raise InvariantViolation("PropertyMedia: rejected media cannot be approved")
        self.status = MediaStatus.UPLOADED

    def reject(self, *, reason: str) -> None:
        self.status = MediaStatus.REJECTED
        self.quarantine_reason = reason

    def is_comparable(self) -> bool:
        return self.status is MediaStatus.UPLOADED and bool(self.perceptual_hash)