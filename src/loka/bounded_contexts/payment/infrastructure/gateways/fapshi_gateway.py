"""Fapshi adapter: the only code in this repository that knows Fapshi exists.

Three properties of this provider shape everything below.

**Authentication is two plain headers.** ``apiuser`` and ``apikey``, with a
separate pair per environment, sent on every call.

**The webhook carries no signature.** Fapshi echoes the secret configured on
the dashboard in an ``x-wh-secret`` header. That identifies the caller; it says
nothing about whether the body arrived intact, and anyone who learns the secret
can post any body at all. So the body is treated as a notification and never as
evidence: :meth:`fetch_status` asks Fapshi what the transaction's status
actually is, and that answer is what settles a payment.

**Fapshi sends one webhook per event and never retries.** A request dropped
during a deploy is money the payer sent and access Kora never granted, in
silence. The status endpoint is therefore also the reconciliation path, which
is why :class:`PaymentStatusSource` is a port with two consumers rather than a
convenience method.

Reference: https://docs.fapshi.com/en/api-reference — endpoints ``initiate-pay``
and ``payment-status``, and the webhook page for ``x-wh-secret``.
"""

from __future__ import annotations

import functools
import hashlib
import hmac
import json
from datetime import UTC, datetime
from typing import Any

import httpx

from loka.bounded_contexts.payment.application.ports import (
    PaymentInitiation,
    VerifiedCallback,
)
from loka.bounded_contexts.payment.domain.exceptions import PaymentGatewayError
from loka.shared.domain.errors import ExternalServiceUnavailable, RateLimited
from loka.shared.infrastructure.config.settings import FapshiSettings
from loka.shared.infrastructure.logging import get_logger

log = get_logger(__name__)

PROVIDER = "fapshi"
SECRET_HEADER = "x-wh-secret"

# Fapshi refuses anything under 100 XAF. Checked here rather than discovered
# from a 400, so a misconfigured fee fails at the call site with the number in
# the message instead of as a provider error nobody can read.
MINIMUM_AMOUNT_XAF = 100

_MAX_ERROR_BODY = 400

# Fapshi's vocabulary on the left, the domain's on the right. The domain has no
# opinion about "SUCCESSFUL" versus "SUCCEEDED"; it has one about which word its
# aggregate accepts, and translation is this adapter's job.
STATUS_MAP = {
    "SUCCESSFUL": "SUCCEEDED",
    "FAILED": "FAILED",
    "EXPIRED": "EXPIRED",
    "PENDING": "PENDING",
    "CREATED": "CREATED",
}


@functools.lru_cache(maxsize=4)
def _pooled_client(base_url: str, timeout: float) -> httpx.AsyncClient:
    """One client per endpoint per process, not one per payment.

    A use case is built per inbound message; a client built with it would pay a
    TLS handshake for every unlock.
    """
    return httpx.AsyncClient(base_url=base_url, timeout=timeout)


class FapshiPaymentGateway:
    """Creates Fapshi payment links and reads back what Fapshi believes."""

    name_id = PROVIDER

    def __init__(
        self,
        settings: FapshiSettings,
        *,
        client: httpx.AsyncClient | None = None,
    ) -> None:
        self._settings = settings
        self._client = client

    def name(self) -> str:
        return self.name_id

    # ------------------------------------------------------------------ writes

    async def initiate(
        self, *, payment_id: str, amount_xaf: int, **kwargs: Any
    ) -> PaymentInitiation:
        """Create a hosted checkout link for one payment.

        ``externalId`` carries our payment id so a Fapshi dashboard row can be
        traced back to a listing without a lookup table, and ``userId`` carries
        the payer so a support question about one person is one query.
        """
        if amount_xaf < MINIMUM_AMOUNT_XAF:
            raise PaymentGatewayError(
                f"Fapshi refuses amounts below {MINIMUM_AMOUNT_XAF} XAF",
                context={"amount_xaf": amount_xaf, "payment_id": payment_id},
            )

        body: dict[str, Any] = {"amount": amount_xaf, "externalId": payment_id}
        user_id = kwargs.get("user_id")
        if user_id:
            body["userId"] = str(user_id)
        message = kwargs.get("message")
        if message:
            body["message"] = str(message)
        email = kwargs.get("email")
        if email:
            body["email"] = str(email)
        if self._settings.redirect_url:
            body["redirectUrl"] = self._settings.redirect_url

        payload = await self._request("POST", "/initiate-pay", json=body)
        trans_id = str(payload.get("transId") or "").strip()
        link = str(payload.get("link") or "").strip()
        if not trans_id or not link:
            # Without both, there is nothing to send the payer and nothing to
            # reconcile later. Treating a 200 with a hollow body as success is
            # how a payment becomes unobservable.
            raise ExternalServiceUnavailable(
                "Fapshi accepted the request without returning a usable payment link",
                context={"payment_id": payment_id, "fields": sorted(payload)},
            )
        log.info(
            "fapshi_payment_initiated",
            payment_id=payment_id,
            trans_id=trans_id,
            amount_xaf=amount_xaf,
        )
        return PaymentInitiation(
            provider=self.name_id,
            provider_reference=trans_id,
            provider_url=link,
            link_ttl_seconds=self._settings.link_ttl_seconds,
            raw={"dateInitiated": payload.get("dateInitiated")},
        )

    # ------------------------------------------------------------------- reads

    def verify_callback(
        self, *, raw_body: bytes, signature: str, headers: dict[str, str]
    ) -> VerifiedCallback:
        """Authenticate the caller and read the transaction it names.

        The verdict returned here is deliberately *not* authoritative: the
        header proves the sender knows the secret, not that this body is what
        Fapshi sent. The caller confirms it with :meth:`fetch_status`.
        """
        expected = self._settings.webhook_secret.get_secret_value()
        if not expected:
            # Fail closed. An unset secret means every caller matches, and an
            # open settlement endpoint is worse than a broken one.
            return _rejected("no Fapshi webhook secret is configured")

        presented = _header(headers, SECRET_HEADER) or signature
        if not presented or not hmac.compare_digest(presented, expected):
            return _rejected("x-wh-secret does not match the configured secret")

        try:
            payload = json.loads(raw_body)
        except ValueError:
            return _rejected("callback body is not JSON")
        if not isinstance(payload, dict):
            return _rejected("callback body is not a JSON object")

        trans_id = str(payload.get("transId") or "").strip()
        if not trans_id:
            return _rejected("callback names no transaction")

        raw_status = str(payload.get("status") or "").strip().upper()
        if raw_status not in STATUS_MAP:
            return _rejected(f"unknown status reported by Fapshi: {raw_status or 'empty'}")

        return VerifiedCallback(
            provider_event_id=_event_id(trans_id, raw_status),
            provider_reference=trans_id,
            accepted=True,
            status=STATUS_MAP[raw_status],
            provider_reported_at=_timestamp(payload),
            payment_id=_external_id(payload),
            audit_digest=hashlib.sha256(raw_body).hexdigest(),
            authoritative=False,
        )

    async def fetch_status(self, *, provider_reference: str) -> VerifiedCallback:
        """What Fapshi says about this transaction, right now.

        Rate limited by Fapshi to six calls per minute per transaction, which
        is ample for one confirmation per webhook plus periodic reconciliation.
        """
        payload = await self._request(
            "GET", f"/payment-status/{provider_reference}", json=None
        )
        raw_status = str(payload.get("status") or "").strip().upper()
        if raw_status not in STATUS_MAP:
            raise ExternalServiceUnavailable(
                "Fapshi reported a status this adapter does not understand",
                context={"trans_id": provider_reference, "status": raw_status},
            )
        trans_id = str(payload.get("transId") or provider_reference)
        return VerifiedCallback(
            provider_event_id=_event_id(trans_id, raw_status),
            provider_reference=trans_id,
            accepted=True,
            status=STATUS_MAP[raw_status],
            provider_reported_at=_timestamp(payload),
            payment_id=_external_id(payload),
            authoritative=True,
        )

    # --------------------------------------------------------------- transport

    def _client_for(self) -> httpx.AsyncClient:
        if self._client is not None:
            return self._client
        return _pooled_client(
            self._settings.base_url, self._settings.request_timeout_seconds
        )

    def _headers(self) -> dict[str, str]:
        return {
            "apiuser": self._settings.api_user.get_secret_value(),
            "apikey": self._settings.api_key.get_secret_value(),
            "content-type": "application/json",
        }

    async def _request(
        self, method: str, path: str, *, json: dict[str, Any] | None
    ) -> dict[str, Any]:
        """One call, with every provider failure turned into a domain error.

        Nothing here retries. An initiate retry could create a second payment
        link for the same unlock, and a status retry is the reconciliation
        job's business, on its own schedule.
        """
        if not self._settings.is_configured():
            raise ExternalServiceUnavailable(
                "Fapshi credentials are not configured",
                context={"base_url": self._settings.base_url},
            )
        client = self._client_for()
        try:
            response = await client.request(
                method, path, json=json, headers=self._headers()
            )
        except httpx.HTTPError as exc:
            raise ExternalServiceUnavailable(
                "Fapshi could not be reached",
                context={"path": path, "error": type(exc).__name__},
            ) from exc

        if response.status_code == 429:
            raise RateLimited(
                "Fapshi rate limit reached", context={"path": path}
            )
        if response.is_error:
            raise ExternalServiceUnavailable(
                "Fapshi rejected the request",
                context={
                    "path": path,
                    "status": response.status_code,
                    # Fapshi always explains itself in `message`; its own docs
                    # say to read that field, so it is what gets logged.
                    "message": _provider_message(response)[:_MAX_ERROR_BODY],
                },
            )
        try:
            body = response.json()
        except ValueError as exc:
            raise ExternalServiceUnavailable(
                "Fapshi returned a body that is not JSON",
                context={"path": path, "status": response.status_code},
            ) from exc
        if not isinstance(body, dict):
            raise ExternalServiceUnavailable(
                "Fapshi returned a JSON value that is not an object",
                context={"path": path, "type": type(body).__name__},
            )
        return body


def _event_id(trans_id: str, raw_status: str) -> str:
    """The identity of an *event*, not of a transaction.

    Fapshi's payload has no event id, and keying on the transaction alone would
    be wrong: a link stays payable after a failed attempt, so one transaction
    can report FAILED and then SUCCESSFUL. Deduplicating on the transaction
    would file the success as a replay and keep the money without granting
    access. The pair is unique per real event and still idempotent on a
    redelivery of the same one.
    """
    return f"{trans_id}:{raw_status}"


def _external_id(payload: dict[str, Any]) -> str | None:
    value = payload.get("externalId")
    return str(value) if value else None


def _provider_message(response: httpx.Response) -> str:
    try:
        body = response.json()
    except ValueError:
        return response.text
    if isinstance(body, dict) and body.get("message"):
        return str(body["message"])
    return response.text


def _rejected(reason: str) -> VerifiedCallback:
    log.warning("fapshi_callback_rejected", reason=reason)
    return VerifiedCallback(
        provider_event_id="",
        provider_reference="",
        accepted=False,
        status="REJECTED",
        rejection_reason=reason,
    )


def _header(headers: dict[str, str], name: str) -> str:
    for key, value in headers.items():
        if key.lower() == name:
            return value
    return ""


def _timestamp(payload: dict[str, Any]) -> datetime | None:
    """When the money moved, preferring confirmation over initiation."""
    for key in ("dateConfirmed", "dateInitiated"):
        parsed = _parse(payload.get(key))
        if parsed is not None:
            return parsed
    return None


def _parse(value: Any) -> datetime | None:
    if value is None:
        return None
    try:
        parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except ValueError:
        return None
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=UTC)
