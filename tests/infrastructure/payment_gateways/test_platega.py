from decimal import Decimal
from types import SimpleNamespace
from unittest.mock import AsyncMock
from uuid import uuid4

import pytest

from src.core.enums import Currency
from src.infrastructure.payment_gateways.platega import PlategaGateway


@pytest.mark.parametrize(
    ("username", "telegram_id", "expected"),
    [
        ("kopota", 463239844, "kopota@t.me"),
        ("@kopota", 463239844, "kopota@t.me"),
        ("  @kopota  ", 463239844, "kopota@t.me"),
        (None, 463239844, "463239844@t.me"),
        ("", 463239844, "463239844@t.me"),
    ],
)
def test_build_user_email(
    username: str | None,
    telegram_id: int | None,
    expected: str,
) -> None:
    assert PlategaGateway._build_user_email(username, telegram_id) == expected


def test_build_user_email_requires_telegram_identity() -> None:
    with pytest.raises(ValueError, match="Telegram username or Telegram ID"):
        PlategaGateway._build_user_email(None, None)


@pytest.mark.asyncio
async def test_create_payment_for_user_passes_telegram_alias() -> None:
    gateway = object.__new__(PlategaGateway)
    expected_result = SimpleNamespace(id=uuid4(), url="https://example.com")
    gateway._create_payment = AsyncMock(return_value=expected_result)
    user = SimpleNamespace(username="@kopota", telegram_id=463239844)

    result = await gateway.handle_create_payment_for_user(
        Decimal("150"),
        "Test payment",
        user,
    )

    assert result is expected_result
    gateway._create_payment.assert_awaited_once_with(
        Decimal("150"),
        "Test payment",
        user_email="kopota@t.me",
    )


@pytest.mark.asyncio
async def test_create_payment_payload_contains_user_email() -> None:
    gateway = object.__new__(PlategaGateway)
    gateway.data = SimpleNamespace(currency=Currency.RUB)
    gateway.settings = SimpleNamespace(payment_method=None)
    gateway._get_bot_redirect_url = AsyncMock(return_value="https://t.me/test_bot")

    payload = await gateway._create_payment_payload(
        Decimal("150"),
        "Test payment",
        "kopota@t.me",
    )

    assert payload["metadata"] == {"user_email": "kopota@t.me"}
    assert payload["paymentDetails"] == {"amount": 150.0, "currency": "RUB"}


def test_redact_payment_payload_does_not_log_user_email() -> None:
    payload = {
        "paymentDetails": {"amount": 150.0, "currency": "RUB"},
        "metadata": {"user_email": "kopota@t.me"},
    }

    redacted = PlategaGateway._redact_payment_payload(payload)

    assert redacted["metadata"] == {"user_email": "***"}
    assert payload["metadata"] == {"user_email": "kopota@t.me"}
