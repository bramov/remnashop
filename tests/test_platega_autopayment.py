from datetime import datetime, timezone
from decimal import Decimal
from types import SimpleNamespace
from typing import Any, cast
from unittest.mock import AsyncMock, patch
from uuid import UUID

import orjson
import pytest
from fastapi import Request
from httpx import Request as HttpxRequest
from httpx import Response
from pydantic import SecretStr

from src.application.dto import (
    PaymentGatewayDto,
    PaymentWebhookResultDto,
)
from src.application.dto.payment_gateway import PlategaGatewaySettingsDto
from src.application.services.autopayment import (
    autopayment_expire_at,
    supports_payment_duration,
)
from src.core.config import AppConfig
from src.core.enums import (
    Currency,
    PaymentGatewayType,
    PlategaAutopaymentInterval,
    TransactionStatus,
)
from src.infrastructure.payment_gateways.platega import PlategaGateway


def _gateway() -> PlategaGateway:
    bot = AsyncMock()
    bot.get_me.return_value = SimpleNamespace(username="remnashop_test_bot")
    client = AsyncMock()
    with patch.object(PlategaGateway, "_make_client", return_value=client):
        gateway = PlategaGateway(
            PaymentGatewayDto(
                type=PaymentGatewayType.PLATEGA,
                currency=Currency.RUB,
                settings=PlategaGatewaySettingsDto(
                    merchant_id="merchant-id",
                    api_key=SecretStr("api-key"),
                ),
            ),
            bot,
            cast(AppConfig, object()),
        )
    return gateway


def _webhook_request(payload: dict[str, Any]) -> Request:
    body = orjson.dumps(payload)
    delivered = False

    async def receive() -> dict[str, Any]:
        nonlocal delivered
        if delivered:
            return {"type": "http.request", "body": b"", "more_body": False}
        delivered = True
        return {"type": "http.request", "body": body, "more_body": False}

    return Request(
        {
            "type": "http",
            "method": "POST",
            "path": "/",
            "headers": [
                (b"x-merchantid", b"merchant-id"),
                (b"x-secret", b"api-key"),
            ],
        },
        receive,
    )


@pytest.mark.parametrize(
    ("duration_days", "interval"),
    [
        (30, PlategaAutopaymentInterval.ONE_MONTH),
        (90, PlategaAutopaymentInterval.THREE_MONTHS),
        (180, PlategaAutopaymentInterval.SIX_MONTHS),
        (365, PlategaAutopaymentInterval.ONE_YEAR),
    ],
)
def test_supported_intervals(duration_days: int, interval: PlategaAutopaymentInterval) -> None:
    assert PlategaAutopaymentInterval.from_duration_days(duration_days) == interval
    assert supports_payment_duration(
        PaymentGatewayType.PLATEGA, duration_days, is_trial=False
    )


def test_unsupported_interval_is_rejected() -> None:
    with pytest.raises(ValueError, match="supports only"):
        PlategaAutopaymentInterval.from_duration_days(60)


def test_expiry_is_six_hours_after_next_charge() -> None:
    next_charge_at = datetime(2026, 9, 1, 12, tzinfo=timezone.utc)
    assert autopayment_expire_at(next_charge_at) == datetime(
        2026, 9, 1, 18, tzinfo=timezone.utc
    )


async def test_create_autopayment_uses_subscription_method_and_interval() -> None:
    gateway = _gateway()
    subscription_id = UUID("11111111-1111-1111-1111-111111111111")
    gateway._client.post.return_value = Response(  # type: ignore[attr-defined]
        200,
        json={
            "transactionId": str(subscription_id),
            "redirect": "https://pay.platega.io/subscription/test",
        },
        request=HttpxRequest("POST", "https://app.platega.io/transaction/process"),
    )

    result = await gateway.handle_create_autopayment(
        Decimal("500"),
        "Premium subscription",
        PlategaAutopaymentInterval.THREE_MONTHS,
    )

    assert result.id == subscription_id
    _, kwargs = gateway._client.post.call_args  # type: ignore[attr-defined]
    assert kwargs["json"]["paymentMethod"] == 6
    assert kwargs["json"]["paymentDetails"] == {
        "amount": 500.0,
        "currency": "RUB",
        "interval": 3,
    }


async def test_charge_webhook_contains_autopayment_metadata() -> None:
    gateway = _gateway()
    payment_id = UUID("33333333-3333-3333-3333-333333333333")
    subscription_id = UUID("11111111-1111-1111-1111-111111111111")

    result = await gateway.handle_webhook(
        _webhook_request(
            {
                "Id": str(payment_id),
                "Amount": 500,
                "Currency": "RUB",
                "Status": "CONFIRMED",
                "PaymentMethod": 6,
                "SubscriptionId": str(subscription_id),
                "NextChargeAt": "2026-09-01T12:00:00Z",
            }
        )
    )

    assert isinstance(result, PaymentWebhookResultDto)
    assert result.payment_id == payment_id
    assert result.autopayment_subscription_id == subscription_id
    assert result.transaction_status == TransactionStatus.COMPLETED
    assert result.payment_method == "6"
    assert result.amount == Decimal("500")
    assert result.currency == Currency.RUB
    assert result.next_charge_at == datetime(2026, 9, 1, 12, tzinfo=timezone.utc)


async def test_subscription_status_webhook_uses_id_as_subscription_id() -> None:
    gateway = _gateway()
    subscription_id = UUID("11111111-1111-1111-1111-111111111111")

    result = await gateway.handle_webhook(
        _webhook_request(
            {
                "Id": str(subscription_id),
                "Amount": 500,
                "Currency": "RUB",
                "Status": "SUBSCRIPTION_ACTIVATED",
                "PaymentMethod": 6,
                "NextChargeAt": "2026-09-01T12:00:00Z",
            }
        )
    )

    assert isinstance(result, PaymentWebhookResultDto)
    assert result.autopayment_subscription_id == subscription_id
    assert result.autopayment_status == "SUBSCRIPTION_ACTIVATED"
    assert result.transaction_status is None
