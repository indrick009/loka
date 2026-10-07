"""Which consumer handles which queue.

Deliberately explicit: every queue and its owner is visible in one place so
operational ownership is unambiguous, and so an unimplemented queue can be told
apart from a broken one.

The registry lives in ``interfaces`` rather than in ``shared`` on purpose:
binding a broker queue to a use case is a composition decision, and
``shared`` must never depend on a bounded context.
"""

from __future__ import annotations

from loka.interfaces.worker.conversation_analysis_consumer import (
    AI_REQUEST_QUEUE,
    ConversationAnalysisConsumer,
)
from loka.interfaces.worker.fraud_consumer import (
    FRAUD_ANALYSIS_QUEUE,
    PAYMENTS_EVENTS_QUEUE,
    FraudAnalysisConsumer,
    PaymentEventsConsumer,
)
from loka.interfaces.worker.search_projection_consumer import (
    SEARCH_PROJECTION_QUEUE,
    SearchProjectionConsumer,
)
from loka.interfaces.worker.whatsapp_inbound_consumer import (
    INBOUND_QUEUE,
    WhatsAppInboundConsumer,
)
from loka.shared.infrastructure.broker.topology import QUEUES
from loka.shared.infrastructure.worker.consumers import ConsumerFactory


def handler_registry() -> dict[str, ConsumerFactory]:
    """Queues with a real handler.

    Everything absent from this mapping is served by ``UnimplementedConsumer``,
    which rejects the message so it is retried and then parked in the DLQ.
    Draining messages silently would lose WhatsApp commands and domain events
    without any trace, so an unimplemented queue stays loud on purpose.
    """
    registry: dict[str, ConsumerFactory] = {
        INBOUND_QUEUE: WhatsAppInboundConsumer,
        AI_REQUEST_QUEUE: ConversationAnalysisConsumer,
        PAYMENTS_EVENTS_QUEUE: PaymentEventsConsumer,
        FRAUD_ANALYSIS_QUEUE: FraudAnalysisConsumer,
        SEARCH_PROJECTION_QUEUE: SearchProjectionConsumer,
    }
    declared = {spec.name for spec in QUEUES}
    unknown = set(registry) - declared
    if unknown:
        # A handler on a queue that does not exist would look wired while
        # never consuming anything: the failure would only show up as silence.
        raise LookupError(f"handlers registered for undeclared queues: {sorted(unknown)}")
    return registry
