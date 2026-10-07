"""Submit landlord verification evidence.

The CNI and the selfie are referenced by object key; the bytes stay in
encrypted object storage and never cross this boundary. A submission that is
complete moves the request to review, where the risk score decides whether it
is handled automatically or queued for a human.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import datetime

from loka.bounded_contexts.landlord.application.dto.landlord_verification_dto import (
    LandlordVerificationView,
)
from loka.bounded_contexts.landlord.domain.entities.verification_request import (
    DocumentKind,
    IdentityDocument,
    VerificationRequest,
    VerificationStatus,
)
from loka.bounded_contexts.landlord.domain.repositories.verification_repository import (
    LANDLORD_PROFILE_REPOSITORY,
    VERIFICATION_REQUEST_REPOSITORY,
)
from loka.shared.application.unit_of_work import UnitOfWork
from loka.shared.application.use_case import UseCase
from loka.shared.domain.errors import ResourceNotFound, ValidationFailed
from loka.shared.domain.identifiers import new_id


@dataclass(frozen=True, slots=True)
class SubmitLandlordVerificationCommand:
    user_id: uuid.UUID
    document_kind: DocumentKind
    document_object_key: str
    document_checksum: str
    selfie_object_key: str
    selfie_checksum: str
    risk_score: int | None = None


class SubmitLandlordVerificationUseCase(
    UseCase[SubmitLandlordVerificationCommand, LandlordVerificationView]
):
    name = "landlord.submit_verification"

    def __init__(self, uow: UnitOfWork) -> None:
        super().__init__(uow)
        self._profiles = uow.repository(LANDLORD_PROFILE_REPOSITORY)
        self._requests = uow.repository(VERIFICATION_REQUEST_REPOSITORY)

    async def execute(  # type: ignore[override]
        self, command: SubmitLandlordVerificationCommand, *, now: datetime
    ) -> LandlordVerificationView:
        profile = await self._profiles.get_by_user(command.user_id)
        if profile is None:
            raise ResourceNotFound(
                "landlord status must be requested before submitting evidence",
                context={"user_id": str(command.user_id)},
            )
        if profile.is_verified:
            raise ValidationFailed(
                "landlord identity is already verified",
                context={"profile_id": str(profile.profile_id)},
            )

        request = await self._requests.latest_for_landlord(profile.profile_id)
        # A rejected attempt is amended in place; an approved one starts a new
        # request so the history keeps both decisions.
        if request is None or request.status is VerificationStatus.VERIFIED:
            request = VerificationRequest(
                request_id=new_id(),
                landlord_id=profile.profile_id,
                user_id=command.user_id,
                now=now,
            )
            is_new = True
        else:
            is_new = False

        request.add_document(
            IdentityDocument(
                document_id=new_id(),
                kind=command.document_kind,
                object_key=command.document_object_key,
                checksum=command.document_checksum,
                captured_at=now,
            ),
            now=now,
        )
        request.attach_selfie(
            object_key=command.selfie_object_key,
            checksum=command.selfie_checksum,
            now=now,
        )
        request.submit(now=now, risk_score=command.risk_score)

        if is_new:
            await self._requests.add(request)
        else:
            await self._requests.save(request)

        self._uow.collect(request)
        await self._uow.commit()
        self._log(
            "landlord_verification_submitted",
            request_id=str(request.request_id),
            review_mode=request.review_mode.value,
        )
        return LandlordVerificationView.from_aggregates(request, profile)