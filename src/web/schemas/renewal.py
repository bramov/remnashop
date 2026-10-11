from datetime import datetime
from typing import Optional
from urllib.parse import urlsplit, urlunsplit

from pydantic import BaseModel, ConfigDict, Field, field_validator

from src.core.enums import PaymentGatewayType


class RenewalResolveRequest(BaseModel):
    model_config = ConfigDict(str_strip_whitespace=True)

    subscription_url: str = Field(min_length=12, max_length=2048)

    @field_validator("subscription_url")
    @classmethod
    def validate_subscription_url(cls, value: str) -> str:
        if any(char.isspace() for char in value):
            raise ValueError("Введите корректную HTTPS-ссылку подписки")
        parsed = urlsplit(value)
        if parsed.scheme != "https" or not parsed.netloc or not parsed.path:
            raise ValueError("Введите корректную HTTPS-ссылку подписки")
        return urlunsplit((parsed.scheme, parsed.netloc, parsed.path, "", ""))


class RenewalSessionResponse(BaseModel):
    token: str
    expires_in: int
    plan_name: str
    subscription_status: str
    expire_at: datetime


class RenewalGatewayPriceResponse(BaseModel):
    gateway_type: PaymentGatewayType
    gateway_name: str
    original_amount: str
    discount_percent: int
    amount: str
    currency: str


class RenewalOfferResponse(BaseModel):
    duration_days: int
    prices: list[RenewalGatewayPriceResponse]


class RenewalOffersResponse(BaseModel):
    plan_name: str
    subscription_status: str
    expire_at: datetime
    offers: list[RenewalOfferResponse]


class RenewalPaymentRequest(BaseModel):
    duration_days: int = Field(gt=0)
    gateway_type: PaymentGatewayType


class RenewalPaymentResponse(BaseModel):
    payment_id: str
    payment_url: Optional[str] = None
    status: str
    amount: str
    currency: str


class RenewalPaymentStatusResponse(BaseModel):
    payment_id: str
    status: str
    subscription_status: str
    expire_at: datetime
