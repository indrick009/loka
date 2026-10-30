"""Mobile Money payments over the platform's gateway (Fapshi in production).

Shared by every paid step outside housing: a bus ticket, a contact unlocked.
"""

from __future__ import annotations

import uuid
from typing import Any

from loka.bounded_contexts.payment.application.ports import PaymentGateway
from loka.shared.domain.errors import DomainError
from loka.shared.infrastructure.logging import get_logger

_logger = get_logger(__name__)


class MobileMoneyPayments:
    def __init__(self, gateway: PaymentGateway) -> None:
        self._gateway = gateway

    async def request(
        self, *, reservation_id: uuid.UUID, amount_xaf: int, payer_phone: str, label: str
    ) -> str | None:
        try:
            initiation = await self._gateway.initiate(
                payment_id=f"booking:{reservation_id}",
                amount_xaf=amount_xaf,
                payer_phone=payer_phone,
                message=label,
            )
        except DomainError as exc:
            _logger.warning("ticket_payment_refused", error=exc.message)
            return None
        return initiation.provider_reference

    async def status(self, reference: str) -> str | None:
        fetch: Any = getattr(self._gateway, "fetch_status", None)
        if fetch is None:
            # The mock gateway answers nothing: in a local demo the withdrawal
            # is taken as validated, which is the one thing it cannot show.
            return "SUCCEEDED" if self._gateway.name() == "mock" else None
        try:
            verdict = await fetch(provider_reference=reference)
        except DomainError as exc:
            _logger.warning("ticket_payment_status_unknown", error=exc.message)
            return None
        return str(verdict.status)
