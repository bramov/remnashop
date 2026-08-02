import hmac
from datetime import datetime
from decimal import Decimal
from typing import Any, Final, Optional, Union, cast
from uuid import UUID

import orjson
from aiogram import Bot
from fastapi import Request
from httpx import AsyncClient, HTTPStatusError
from loguru import logger

from src.application.dto import (
    PaymentGatewayDto,
    PaymentResultDto,
    PaymentWebhookResultDto,
)
from src.application.dto.payment_gateway import PlategaGatewaySettingsDto
from src.core.config import AppConfig
from src.core.enums import Currency, PlategaAutopaymentInterval, TransactionStatus

from .base import BasePaymentGateway


# https://docs.platega.io/
class PlategaGateway(BasePaymentGateway):
    _client: AsyncClient

    API_BASE: Final[str] = "https://app.platega.io"
    DEFAULT_MULTI_METHOD_ENDPOINT: Final[str] = "v2/transaction/process"
    DEFAULT_SINGLE_METHOD_ENDPOINT: Final[str] = "transaction/process"

    def __init__(self, gateway: PaymentGatewayDto, bot: Bot, config: AppConfig) -> None:
        super().__init__(gateway, bot, config)

        if not isinstance(self.data.settings, PlategaGatewaySettingsDto):
            raise TypeError(
                f"Invalid settings type: expected {PlategaGatewaySettingsDto.__name__}, "
                f"got {type(self.data.settings).__name__}"
            )

        self.settings = cast(PlategaGatewaySettingsDto, self.data.settings)
        if self.settings.merchant_id is None or self.settings.api_key is None:
            raise ValueError("Platega gateway is not configured")

        self.merchant_id = self.settings.merchant_id
        self.api_key = self.settings.api_key.get_secret_value()

        self._client = self._make_client(
            base_url=self.API_BASE,
            headers={
                "X-MerchantId": self.merchant_id,
                "X-Secret": self.api_key,
                "Content-Type": "application/json",
                "Accept": "application/json",
            },
        )
    async def handle_create_payment(self, amount: Decimal, details: str) -> PaymentResultDto:
        payload = await self._create_payment_payload(amount, details)
        logger.debug(f"Creating payment payload: {payload}")
        endpoint = (
            self.DEFAULT_SINGLE_METHOD_ENDPOINT
            if self.settings.payment_method is not None
            else self.DEFAULT_MULTI_METHOD_ENDPOINT
        )

        try:
            response = await self._client.post(endpoint, json=payload)
            response.raise_for_status()
            data = orjson.loads(response.content)
            return self._get_payment_data(data)

        except HTTPStatusError as e:
            logger.error(
                f"HTTP error creating payment. "
                f"Status: '{e.response.status_code}', Body: {e.response.text}"
            )
            raise
        except (KeyError, orjson.JSONDecodeError) as e:
            logger.error(f"Failed to parse response. Error: {e}")
            raise
        except Exception as e:
            logger.exception(f"An unexpected error occurred while creating payment: {e}")
            raise

    async def handle_create_autopayment(
        self,
        amount: Decimal,
        details: str,
        interval: PlategaAutopaymentInterval,
    ) -> PaymentResultDto:
        payload = await self._create_payment_payload(amount, details)
        payload["paymentMethod"] = 6
        payload["paymentDetails"]["interval"] = int(interval)

        try:
            response = await self._client.post(self.DEFAULT_SINGLE_METHOD_ENDPOINT, json=payload)
            response.raise_for_status()
            return self._get_payment_data(orjson.loads(response.content))
        except HTTPStatusError as error:
            logger.error(
                "HTTP error creating Platega autopayment. "
                f"Status: '{error.response.status_code}', Body: {error.response.text}"
            )
            raise

    async def handle_cancel_autopayment(self, subscription_id: UUID) -> None:
        response = await self._client.post(f"subscription/{subscription_id}/cancel")
        response.raise_for_status()
        logger.info(f"Canceled Platega autopayment '{subscription_id}'")

    async def handle_webhook(
        self, request: Request
    ) -> Union[tuple[UUID, TransactionStatus], PaymentWebhookResultDto, None]:
        logger.debug(f"Received {self.__class__.__name__} webhook request")

        if not self._verify_webhook(request):
            raise PermissionError("Webhook verification failed")

        raw_body = await request.body()
        webhook_data = orjson.loads(raw_body)

        payment_id_str = self._get_value(webhook_data, "id", "Id")
        if not payment_id_str:
            raise ValueError("Required field 'id' is missing")

        raw_status = self._get_value(webhook_data, "status", "Status")
        if not raw_status:
            raise ValueError("Required field 'status' is missing")

        status = str(raw_status).upper()
        payment_id = UUID(payment_id_str)
        payment_method = self._normalize_payment_method(
            self._get_value(webhook_data, "paymentMethod", "PaymentMethod")
        )
        subscription_id_raw = self._get_value(
            webhook_data, "subscriptionId", "SubscriptionId"
        )
        subscription_id = UUID(subscription_id_raw) if subscription_id_raw else None
        next_charge_at = self._parse_datetime(
            self._get_value(webhook_data, "nextChargeAt", "NextChargeAt")
        )
        amount_raw = self._get_value(webhook_data, "amount", "Amount")
        amount = Decimal(str(amount_raw)) if amount_raw is not None else None
        currency_raw = self._get_value(webhook_data, "currency", "Currency")
        currency = Currency.from_code(str(currency_raw)) if currency_raw else None

        transaction_status: Optional[TransactionStatus]
        match status:
            case "CONFIRMED":
                transaction_status = TransactionStatus.COMPLETED
            case "CANCELED":
                transaction_status = TransactionStatus.CANCELED
            case "CHARGEBACKED":
                transaction_status = TransactionStatus.REFUNDED
            case value if value.startswith("SUBSCRIPTION_"):
                transaction_status = None
            case _:
                raise ValueError(f"Unsupported status: {status}")

        if status.startswith("SUBSCRIPTION_") and subscription_id is None:
            subscription_id = payment_id

        return PaymentWebhookResultDto(
            payment_id=payment_id,
            transaction_status=transaction_status,
            payment_method=payment_method,
            autopayment_subscription_id=subscription_id,
            autopayment_status=status if status.startswith("SUBSCRIPTION_") else None,
            next_charge_at=next_charge_at,
            amount=amount,
            currency=currency,
        )

    async def _create_payment_payload(self, amount: Decimal, details: str) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "paymentDetails": {
                "amount": float(amount),
                "currency": self.data.currency.value,
            },
            "description": details,
            "return": await self._get_bot_redirect_url(),
            "failedUrl": await self._get_bot_redirect_url(),
        }
        if self.settings.payment_method is not None:
            payload["paymentMethod"] = self.settings.payment_method

        return payload

    def _get_payment_data(self, data: dict[str, Any]) -> PaymentResultDto:
        transaction_id_str = data.get("transactionId")
        if not transaction_id_str:
            raise KeyError("Invalid response from API: missing 'transactionId'")

        payment_url = data.get("redirect") or data.get("url")
        if not payment_url:
            raise KeyError("Invalid response from API: missing 'redirect' or 'url'")

        return PaymentResultDto(id=UUID(transaction_id_str), url=str(payment_url))

    @staticmethod
    def _normalize_payment_method(value: Any) -> str | None:
        if value is None:
            return None

        payment_method = str(value).strip()
        return payment_method or None

    @staticmethod
    def _get_value(data: dict[str, Any], *keys: str) -> Any:
        for key in keys:
            if key in data:
                return data[key]
        return None

    @staticmethod
    def _parse_datetime(value: Any) -> Optional[datetime]:
        if not value:
            return None
        if isinstance(value, datetime):
            return value
        return datetime.fromisoformat(str(value).replace("Z", "+00:00"))

    def _verify_webhook(self, request: Request) -> bool:
        merchant_id = request.headers.get("X-MerchantId")
        secret = request.headers.get("X-Secret")

        if not merchant_id or not secret:
            logger.warning("Webhook is missing X-MerchantId or X-Secret headers")
            return False

        merchant_id_ok = hmac.compare_digest(merchant_id, self.merchant_id)
        secret_ok = hmac.compare_digest(secret, self.api_key)

        if not merchant_id_ok or not secret_ok:
            logger.warning("Invalid Platega webhook credentials")
            return False

        return True
