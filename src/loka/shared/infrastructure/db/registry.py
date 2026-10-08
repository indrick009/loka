"""Model registry.

Importing this module populates ``Base.metadata`` with every table, which is
what Alembic autogeneration and ``create_all`` rely on.
"""

from __future__ import annotations

from loka.bounded_contexts.ai.infrastructure.persistence.models import AiUsageLedgerRow
from loka.bounded_contexts.fraud.infrastructure.persistence.models import (
    FraudSignalRow,
    MediaHashRow,
    ReportRow,
    RiskProfileRow,
    TrustProfileRow,
)
from loka.bounded_contexts.identity.infrastructure.persistence.models import (
    PhoneVerificationChallengeRow,
    RefreshTokenRow,
    UserRow,
)
from loka.bounded_contexts.landlord.infrastructure.persistence.models import (
    LandlordProfileRow,
    VerificationDocumentRow,
    VerificationRequestRow,
)
from loka.bounded_contexts.messaging.infrastructure.persistence.models import (
    ConversationMetricsRow,
    ConversationSessionRow,
    ConversationTurnRow,
    InboundMessageRow,
    OutboundMessageRow,
)
from loka.bounded_contexts.payment.infrastructure.persistence.models import (
    PaymentCallbackRow,
    ServiceAccessGrantRow,
    ServiceFeePaymentRow,
)
from loka.bounded_contexts.property.infrastructure.persistence.models import (
    AvailabilityCheckRow,
    PropertyDataQualityIssueRow,
    PropertyMediaRow,
    PropertyRow,
)
from loka.bounded_contexts.rental.infrastructure.persistence.models import (
    RentalApplicationRow,
)
from loka.bounded_contexts.visit.infrastructure.persistence.models import VisitRow
from loka.shared.infrastructure.db.audit import AuditLog
from loka.shared.infrastructure.db.base import Base
from loka.shared.infrastructure.db.idempotency import IdempotencyKey, WebhookReplayGuard
from loka.shared.infrastructure.db.outbox import OutboxRecord

__all__ = [
    "AiUsageLedgerRow",
    "AuditLog",
    "AvailabilityCheckRow",
    "Base",
    "ConversationMetricsRow",
    "ConversationSessionRow",
    "ConversationTurnRow",
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
    "RefreshTokenRow",
    "RentalApplicationRow",
    "ReportRow",
    "RiskProfileRow",
    "ServiceAccessGrantRow",
    "ServiceFeePaymentRow",
    "TrustProfileRow",
    "UserRow",
    "VerificationDocumentRow",
    "VerificationRequestRow",
    "VisitRow",
    "WebhookReplayGuard",
]