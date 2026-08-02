from datetime import datetime
from decimal import Decimal
from typing import Optional
from uuid import UUID

from dishka.integrations.taskiq import FromDishka, inject

from src.application.use_cases.gateways.commands.autopayment import (
    ProcessPlategaAutopayment,
    ProcessPlategaAutopaymentDto,
)
from src.application.use_cases.gateways.commands.payment import ProcessPayment, ProcessPaymentDto
from src.application.use_cases.misc.commands.maintenance import CancelOldTransactions
from src.core.enums import Currency, PaymentGatewayType, TransactionStatus
from src.infrastructure.taskiq.broker import broker


@broker.task()
@inject(patch_module=True)
async def handle_payment_transaction_task(
    payment_id: UUID,
    payment_status: TransactionStatus,
    gateway_type: PaymentGatewayType,
    process_payment: FromDishka[ProcessPayment],
) -> None:
    await process_payment.system(
        ProcessPaymentDto(
            payment_id=payment_id,
            new_transaction_status=payment_status,
            gateway_type=gateway_type,
        )
    )


@broker.task()
@inject(patch_module=True)
async def handle_platega_autopayment_task(
    payment_id: UUID,
    subscription_id: UUID,
    transaction_status: Optional[TransactionStatus],
    autopayment_status: Optional[str],
    next_charge_at: Optional[datetime],
    amount: Optional[str],
    currency: Optional[str],
    payment_method: Optional[str],
    process_autopayment: FromDishka[ProcessPlategaAutopayment],
) -> None:
    await process_autopayment.system(
        ProcessPlategaAutopaymentDto(
            payment_id=payment_id,
            subscription_id=subscription_id,
            transaction_status=transaction_status,
            autopayment_status=autopayment_status,
            next_charge_at=next_charge_at,
            amount=Decimal(amount) if amount is not None else None,
            currency=Currency.from_code(currency) if currency is not None else None,
            payment_method=payment_method,
        )
    )


@broker.task(schedule=[{"cron": "*/30 * * * *"}])
@inject(patch_module=True)
async def cancel_old_transactions_task(
    cancel_old_transactions: FromDishka[CancelOldTransactions],
) -> None:
    await cancel_old_transactions.system()
