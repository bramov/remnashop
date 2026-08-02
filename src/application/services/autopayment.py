from datetime import datetime, timedelta

from src.core.constants import PLATEGA_AUTOPAYMENT_GRACE_HOURS
from src.core.enums import PaymentGatewayType, PlategaAutopaymentInterval


def supports_payment_duration(
    gateway_type: PaymentGatewayType,
    duration_days: int,
    *,
    is_trial: bool,
) -> bool:
    if gateway_type != PaymentGatewayType.PLATEGA or is_trial:
        return True
    try:
        PlategaAutopaymentInterval.from_duration_days(duration_days)
    except ValueError:
        return False
    return True


def autopayment_expire_at(next_charge_at: datetime) -> datetime:
    return next_charge_at + timedelta(hours=PLATEGA_AUTOPAYMENT_GRACE_HOURS)
