from datetime import datetime
from typing import Any, Optional
from uuid import UUID

from sqlalchemy import ForeignKey, Integer
from sqlalchemy.orm import Mapped, mapped_column, relationship

from src.core.enums import (
    Currency,
    PlategaAutopaymentInterval,
    PlategaAutopaymentStatus,
    PurchaseType,
)

from .base import BaseSql
from .timestamp import TimestampMixin
from .user import User


class PlategaAutopayment(BaseSql, TimestampMixin):
    __tablename__ = "platega_autopayments"

    id: Mapped[int] = mapped_column(primary_key=True)
    subscription_id: Mapped[UUID] = mapped_column(index=True, unique=True)
    user_id: Mapped[int] = mapped_column(
        Integer,
        ForeignKey("users.id", ondelete="CASCADE"),
        index=True,
    )
    status: Mapped[PlategaAutopaymentStatus] = mapped_column(index=True)
    interval: Mapped[PlategaAutopaymentInterval]
    purchase_type: Mapped[PurchaseType]
    pricing: Mapped[dict[str, Any]]
    currency: Mapped[Currency]
    plan_snapshot: Mapped[dict[str, Any]]
    gateway_display_name: Mapped[Optional[str]]
    replaces_subscription_id: Mapped[Optional[UUID]]
    next_charge_at: Mapped[Optional[datetime]]
    last_charge_at: Mapped[Optional[datetime]]

    user: Mapped["User"] = relationship(foreign_keys=[user_id])
