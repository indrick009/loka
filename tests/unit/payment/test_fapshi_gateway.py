"""What the Fapshi adapter promises, and the two traps it exists to avoid.

Fapshi authenticates its webhooks by echoing a shared secret in an
``x-wh-secret`` header. That is not a signature: it proves the caller knows the
secret and says nothing about the body, so anyone who learns it can post any
status at all. The adapter therefore marks a webhook verdict as *not*
authoritative and offers ``fetch_status`` as the thing that is.

Fapshi also sends one webhook per event and never retries. The identity of an
event is consequently the pair (transaction, status), not the transaction: a
payment link stays payable after a failed attempt, so one transaction really
does report FAILED and later SUCCESSFUL, and keying on the transaction alone
would file that success as a duplicate — money kept, contact never revealed.

No test here reaches the network; every call goes through
``httpx.MockTransport``.
"""

from __future__ import annotations

import hashlib
import json
from typing import Any

import httpx
import pytest

from loka.bounded_contexts.payment.domain.exceptions import PaymentGatewayError
from loka.bounded_contexts.payment.infrastructure.gateways.fapshi_gateway import (
    FapshiPaymentGateway,
)
from loka.shared.domain.errors import ExternalServiceUnavailable, RateLimited
from loka.shared.infrastructure.config.settings import FapshiSettings

pytestmark = pytest.mark.unit

SECRET = "wh-secret-value"


def settings(**overrides: Any) -> FapshiSettings:
    base: dict[str, Any] = {
        "base_url": "https://sandbox.fapshi.com",
        "api_user": "user",
        "api_key": "key",
        "webhook_secret": SECRET,
    }
    base.update(overrides)
    return FapshiSettings(**base)


def gateway_answering(
    handler: Any, *, config: FapshiSettings | None = None
) -> FapshiPaymentGateway:
    client = httpx.AsyncClient(
        base_url="https://sandbox.fapshi.com", transport=httpx.MockTransport(handler)
    )
    return FapshiPaymentGateway(config or settings(), client=client)


def a_transaction(**overrides: Any) -> dict[str, Any]:
    body: dict[str, Any] = {
        "transId": "tx_1",
        "status": "SUCCESSFUL",
        "medium": "mobile money",
        "amount": 1000,
        "externalId": "pay-1",
        "dateInitiated": "2026-10-11T09:00:00Z",
        "dateConfirmed": "2026-10-11T09:01:00Z",
    }
    body.update(overrides)
    return body


class TestCreatingAPaymentLink:
    @pytest.mark.asyncio
    async def test_the_payer_gets_a_link_and_the_platform_a_reference(self) -> None:
        def handler(request: httpx.Request) -> httpx.Response:
            assert request.url.path == "/initiate-pay"
            assert request.headers["apiuser"] == "user"
            assert request.headers["apikey"] == "key"
            body = json.loads(request.content)
            assert body["amount"] == 1000
            assert body["externalId"] == "pay-1"
            return httpx.Response(
                200,
                json={
                    "message": "ok",
                    "link": "https://checkout.fapshi.com/pay/tx_1",
                    "transId": "tx_1",
                    "dateInitiated": "2026-10-11T09:00:00Z",
                },
            )

        initiation = await gateway_answering(handler).initiate(
            payment_id="pay-1", amount_xaf=1000
        )

        assert initiation.provider == "fapshi"
        assert initiation.provider_reference == "tx_1"
        assert initiation.provider_url == "https://checkout.fapshi.com/pay/tx_1"

    @pytest.mark.asyncio
    async def test_the_providers_own_link_lifetime_is_carried_back(self) -> None:
        """24 hours, not the platform's half-hour guess.

        Told the link died at 30 minutes, a payer who comes back at hour two is
        turned away from a page Fapshi would still have honoured.
        """

        def handler(_: httpx.Request) -> httpx.Response:
            return httpx.Response(
                200, json={"link": "https://c.fapshi.com/1", "transId": "tx_1"}
            )

        initiation = await gateway_answering(handler).initiate(
            payment_id="pay-1", amount_xaf=1000
        )

        assert initiation.link_ttl_seconds == 86_400

    @pytest.mark.asyncio
    async def test_an_amount_below_the_providers_floor_is_refused_here(self) -> None:
        def handler(_: httpx.Request) -> httpx.Response:  # pragma: no cover
            raise AssertionError("the provider should never have been called")

        with pytest.raises(PaymentGatewayError):
            await gateway_answering(handler).initiate(payment_id="pay-1", amount_xaf=99)

    @pytest.mark.asyncio
    async def test_a_200_without_a_link_is_a_failure_not_a_success(self) -> None:
        """A payment with no link and no reference can never be paid or traced."""

        def handler(_: httpx.Request) -> httpx.Response:
            return httpx.Response(200, json={"message": "ok"})

        with pytest.raises(ExternalServiceUnavailable):
            await gateway_answering(handler).initiate(payment_id="pay-1", amount_xaf=1000)

    @pytest.mark.asyncio
    async def test_the_providers_explanation_is_not_swallowed(self) -> None:
        def handler(_: httpx.Request) -> httpx.Response:
            return httpx.Response(403, json={"message": "Invalid API credentials"})

        with pytest.raises(ExternalServiceUnavailable) as caught:
            await gateway_answering(handler).initiate(payment_id="pay-1", amount_xaf=1000)

        assert "Invalid API credentials" in str(caught.value.context)

    @pytest.mark.asyncio
    async def test_missing_credentials_never_reach_the_network(self) -> None:
        def handler(_: httpx.Request) -> httpx.Response:  # pragma: no cover
            raise AssertionError("an unconfigured gateway must not call out")

        unconfigured = gateway_answering(handler, config=settings(api_key=""))

        with pytest.raises(ExternalServiceUnavailable):
            await unconfigured.initiate(payment_id="pay-1", amount_xaf=1000)


class TestAuthenticatingAWebhook:
    def verify(self, body: dict[str, Any], *, secret: str | None = SECRET) -> Any:
        def handler(_: httpx.Request) -> httpx.Response:  # pragma: no cover
            raise AssertionError("verification is offline")

        raw = json.dumps(body).encode()
        headers = {} if secret is None else {"x-wh-secret": secret}
        return gateway_answering(handler).verify_callback(
            raw_body=raw, signature="", headers=headers
        )

    def test_the_right_secret_is_accepted(self) -> None:
        verdict = self.verify(a_transaction())

        assert verdict.accepted
        assert verdict.status == "SUCCEEDED"
        assert verdict.provider_reference == "tx_1"

    def test_a_wrong_secret_is_rejected(self) -> None:
        verdict = self.verify(a_transaction(), secret="not-the-secret")

        assert not verdict.accepted

    def test_a_missing_header_is_rejected(self) -> None:
        verdict = self.verify(a_transaction(), secret=None)

        assert not verdict.accepted

    def test_an_unconfigured_secret_rejects_everyone(self) -> None:
        """Fail closed: with no secret set, every caller would match."""

        def handler(_: httpx.Request) -> httpx.Response:  # pragma: no cover
            raise AssertionError("verification is offline")

        verdict = gateway_answering(
            handler, config=settings(webhook_secret="")
        ).verify_callback(
            raw_body=json.dumps(a_transaction()).encode(),
            signature="",
            headers={"x-wh-secret": ""},
        )

        assert not verdict.accepted

    @pytest.mark.parametrize("body", [b"not json", b"[]", b'{"status": "SUCCESSFUL"}'])
    def test_a_body_that_names_no_transaction_is_rejected(self, body: bytes) -> None:
        def handler(_: httpx.Request) -> httpx.Response:  # pragma: no cover
            raise AssertionError("verification is offline")

        verdict = gateway_answering(handler).verify_callback(
            raw_body=body, signature="", headers={"x-wh-secret": SECRET}
        )

        assert not verdict.accepted

    def test_an_unknown_status_is_rejected_rather_than_guessed(self) -> None:
        verdict = self.verify(a_transaction(status="REVERSED"))

        assert not verdict.accepted

    def test_the_stored_digest_is_of_the_body_never_the_secret(self) -> None:
        """The header is a live credential; the callback log is not its home."""
        raw = json.dumps(a_transaction()).encode()

        verdict = self.verify(a_transaction())

        assert verdict.audit_digest == hashlib.sha256(raw).hexdigest()
        assert SECRET not in (verdict.audit_digest or "")

    def test_a_pushed_verdict_is_never_authoritative(self) -> None:
        verdict = self.verify(a_transaction())

        assert verdict.authoritative is False


class TestEventIdentity:
    def verify(self, status: str) -> str:
        def handler(_: httpx.Request) -> httpx.Response:  # pragma: no cover
            raise AssertionError("verification is offline")

        return str(
            gateway_answering(handler)
            .verify_callback(
                raw_body=json.dumps(a_transaction(status=status)).encode(),
                signature="",
                headers={"x-wh-secret": SECRET},
            )
            .provider_event_id
        )

    def test_a_failure_then_a_success_are_two_events(self) -> None:
        """The case that makes the transaction id the wrong dedup key.

        A link stays payable after a failed attempt. Keyed on the transaction,
        the later SUCCESSFUL would be filed as a replay of the FAILED and the
        payer would be charged without being served.
        """
        assert self.verify("FAILED") != self.verify("SUCCESSFUL")

    def test_the_same_event_twice_is_one_event(self) -> None:
        assert self.verify("SUCCESSFUL") == self.verify("SUCCESSFUL")


class TestAskingTheProvider:
    @pytest.mark.asyncio
    async def test_a_fetched_verdict_is_authoritative(self) -> None:
        def handler(request: httpx.Request) -> httpx.Response:
            assert request.url.path == "/payment-status/tx_1"
            return httpx.Response(200, json=a_transaction())

        verdict = await gateway_answering(handler).fetch_status(provider_reference="tx_1")

        assert verdict.authoritative is True
        assert verdict.status == "SUCCEEDED"
        assert verdict.payment_id == "pay-1"

    @pytest.mark.asyncio
    async def test_the_confirmation_time_is_preferred_over_the_initiation_time(
        self,
    ) -> None:
        def handler(_: httpx.Request) -> httpx.Response:
            return httpx.Response(200, json=a_transaction())

        verdict = await gateway_answering(handler).fetch_status(provider_reference="tx_1")

        assert verdict.provider_reported_at is not None
        assert verdict.provider_reported_at.minute == 1

    @pytest.mark.parametrize(
        ("reported", "expected"),
        [
            ("SUCCESSFUL", "SUCCEEDED"),
            ("FAILED", "FAILED"),
            ("EXPIRED", "EXPIRED"),
            ("PENDING", "PENDING"),
            ("CREATED", "CREATED"),
        ],
    )
    @pytest.mark.asyncio
    async def test_every_provider_status_has_one_domain_word(
        self, reported: str, expected: str
    ) -> None:
        def handler(_: httpx.Request) -> httpx.Response:
            return httpx.Response(200, json=a_transaction(status=reported))

        verdict = await gateway_answering(handler).fetch_status(provider_reference="tx_1")

        assert verdict.status == expected

    @pytest.mark.asyncio
    async def test_a_rate_limit_says_stop_asking(self) -> None:
        """Raised, not swallowed: six reads a minute per transaction is the cap."""

        def handler(_: httpx.Request) -> httpx.Response:
            return httpx.Response(429, json={"message": "Too many requests"})

        with pytest.raises(RateLimited):
            await gateway_answering(handler).fetch_status(provider_reference="tx_1")

    @pytest.mark.asyncio
    async def test_an_unreachable_provider_is_a_domain_error(self) -> None:
        def handler(_: httpx.Request) -> httpx.Response:
            raise httpx.ConnectTimeout("no route")

        with pytest.raises(ExternalServiceUnavailable):
            await gateway_answering(handler).fetch_status(provider_reference="tx_1")

    @pytest.mark.asyncio
    async def test_a_status_the_adapter_cannot_map_is_not_invented(self) -> None:
        def handler(_: httpx.Request) -> httpx.Response:
            return httpx.Response(200, json=a_transaction(status="REVERSED"))

        with pytest.raises(ExternalServiceUnavailable):
            await gateway_answering(handler).fetch_status(provider_reference="tx_1")
