from dataclasses import dataclass
from datetime import datetime
from typing import Optional
from uuid import UUID

from src.core.enums import (
    Currency,
    PlategaAutopaymentInterval,
    PlategaAutopaymentStatus,
    PurchaseType,
)

from .base import BaseDto, TimestampMixin, TrackableMixin
from .plan import PlanSnapshotDto
from .transaction import PriceDetailsDto


@dataclass(kw_only=True)
class PlategaAutopaymentDto(BaseDto, TrackableMixin, TimestampMixin):
    subscription_id: UUID
    user_id: int
    status: PlategaAutopaymentStatus = PlategaAutopaymentStatus.PENDING
    interval: PlategaAutopaymentInterval
    purchase_type: PurchaseType
    pricing: PriceDetailsDto
    currency: Currency
    plan_snapshot: PlanSnapshotDto
    gateway_display_name: Optional[str] = None
    replaces_subscription_id: Optional[UUID] = None
    next_charge_at: Optional[datetime] = None
    last_charge_at: Optional[datetime] = None
