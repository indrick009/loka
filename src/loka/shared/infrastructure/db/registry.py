"""Model registry.

Importing this module populates ``Base.metadata`` with every table, which is
what Alembic autogeneration and ``create_all`` rely on.
"""

from __future__ import annotations

from loka.bounded_contexts.ai.infrastructure.models import (
    AiUsageLedgerRow,
    DecisionShadowRow,
)
from loka.bounded_contexts.auth.infrastructure.persistence.models import (
    PhoneVerificationChallengeRow,
    RefreshTokenRow,
    UserRow,
)
from loka.bounded_contexts.booking.infrastructure.models import (
    BookingAgencyRow,
    BookingLineRow,
    BookingReservationRow,
)
from loka.bounded_contexts.conversation.infrastructure.persistence.messaging_models import (
    ConversationMetricsRow,
    ConversationSessionRow,
    ConversationTurnRow,
    InboundMessageRow,
    OutboundMessageRow,
)
from loka.bounded_contexts.dating.infrastructure.models import DatingAnswerRow, DatingProfileRow
from loka.bounded_contexts.food.infrastructure.models import (
    FoodDailyMenuRow,
    FoodRequestRow,
    FoodRestaurantRow,
)
from loka.bounded_contexts.found.infrastructure.models import FoundClaimRow, FoundDeclarationRow
from loka.bounded_contexts.fraud.infrastructure.persistence.models import (
    FraudSignalRow,
    MediaHashRow,
    ReportRow,
    RiskProfileRow,
    TrustProfileRow,
)
from loka.bounded_contexts.housing.infrastructure.persistence.models import (
    AvailabilityCheckRow,
    LandlordProfileRow,
    PropertyDataQualityIssueRow,
    PropertyMediaRow,
    PropertyRow,
    TenantDemandRow,
)
from loka.bounded_contexts.payment.infrastructure.persistence.models import (
    PaymentCallbackRow,
    ServiceAccessGrantRow,
    ServiceFeePaymentRow,
)
from loka.bounded_contexts.payment.infrastructure.unlock_store import ContactUnlockRow
from loka.bounded_contexts.reviews_complaints.infrastructure.persistence.models import (
    ProviderComplaintRow,
    ProviderReviewRow,
    ProviderStandingRow,
)
from loka.shared.infrastructure.db.audit import AuditLog
from loka.shared.infrastructure.db.base import Base
from loka.shared.infrastructure.db.idempotency import IdempotencyKey, WebhookReplayGuard
from loka.shared.infrastructure.db.outbox import OutboxRecord

__all__ = [
    "AiUsageLedgerRow",
    "AuditLog",
    "AvailabilityCheckRow",
    "Base",
    "BookingAgencyRow",
    "BookingLineRow",
    "BookingReservationRow",
    "ContactUnlockRow",
    "ConversationMetricsRow",
    "ConversationSessionRow",
    "ConversationTurnRow",
    "DatingAnswerRow",
    "DatingProfileRow",
    "DecisionShadowRow",
    "FoodDailyMenuRow",
    "FoodRequestRow",
    "FoodRestaurantRow",
    "FoundClaimRow",
    "FoundDeclarationRow",
    "FraudSignalRow",
    "IdempotencyKey",
    "InboundMessageRow",
    "LandlordProfileRow",
    "MediaHashRow",
    "OutboundMessageRow",
    "OutboxRecord",
    "PaymentCallbackRow",
    "PhoneVerificationChallengeRow",
    "PropertyDataQualityIssueRow",
    "PropertyMediaRow",
    "PropertyRow",
    "ProviderComplaintRow",
    "ProviderReviewRow",
    "ProviderStandingRow",
    "RefreshTokenRow",
    "ReportRow",
    "RiskProfileRow",
    "ServiceAccessGrantRow",
    "ServiceFeePaymentRow",
    "TenantDemandRow",
    "TrustProfileRow",
    "UserRow",
    "WebhookReplayGuard",
]