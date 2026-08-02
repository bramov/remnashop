from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal
from typing import Optional
from uuid import UUID

from loguru import logger

from src.application.common import EventPublisher, Interactor
from src.application.common.dao import (
    PlategaAutopaymentDao,
    TransactionDao,
    UserDao,
)
from src.application.common.uow import UnitOfWork
from src.application.dto import PlategaAutopaymentDto, TransactionDto, UserDto
from src.application.events import AutopaymentFailedEvent
from src.application.services.autopayment import autopayment_expire_at
from src.application.use_cases.gateways.queries.providers import GetPaymentGatewayInstance
from src.core.enums import (
    Currency,
    PaymentGatewayType,
    PlategaAutopaymentStatus,
    PurchaseType,
    TransactionStatus,
)
from src.core.utils.time import datetime_now

from .payment import ProcessPayment, ProcessPaymentDto


@dataclass(frozen=True)
class ProcessPlategaAutopaymentDto:
    payment_id: UUID
    subscription_id: UUID
    transaction_status: Optional[TransactionStatus]
    autopayment_status: Optional[str]
    next_charge_at: Optional[datetime]
    amount: Optional[Decimal]
    currency: Optional[Currency]
    payment_method: Optional[str]


class ProcessPlategaAutopayment(Interactor[ProcessPlategaAutopaymentDto, None]):
    required_permission = None

    def __init__(
        self,
        uow: UnitOfWork,
        autopayment_dao: PlategaAutopaymentDao,
        transaction_dao: TransactionDao,
        user_dao: UserDao,
        process_payment: ProcessPayment,
        get_payment_gateway_instance: GetPaymentGatewayInstance,
        event_publisher: EventPublisher,
    ) -> None:
        self.uow = uow
        self.autopayment_dao = autopayment_dao
        self.transaction_dao = transaction_dao
        self.user_dao = user_dao
        self.process_payment = process_payment
        self.get_payment_gateway_instance = get_payment_gateway_instance
        self.event_publisher = event_publisher

    async def _execute(  # noqa: C901
        self, actor: UserDto, data: ProcessPlategaAutopaymentDto
    ) -> None:
        async with self.uow:
            autopayment = await self.autopayment_dao.get_by_subscription_id(
                data.subscription_id
            )
            if autopayment is None:
                logger.warning(
                    f"Ignoring Platega autopayment callback for unknown subscription "
                    f"'{data.subscription_id}'"
                )
                return

            if (
                data.transaction_status == TransactionStatus.CANCELED
                and autopayment.status
                in {
                    PlategaAutopaymentStatus.CANCELED,
                    PlategaAutopaymentStatus.REPLACED,
                }
            ):
                logger.info(
                    f"Ignoring failed charge callback for inactive Platega autopayment "
                    f"'{data.subscription_id}'"
                )
                return

            user = await self.user_dao.get_by_id(autopayment.user_id)
            if user is None:
                logger.error(
                    f"User '{autopayment.user_id}' not found for Platega autopayment "
                    f"'{data.subscription_id}'"
                )
                return

            if data.autopayment_status is not None:
                await self._process_status_callback(autopayment, data)
                await self.uow.commit()
                return

            self._validate_charge(autopayment.pricing.final_amount, autopayment.currency, data)
            transaction = await self.transaction_dao.get_by_payment_id(data.payment_id)
            is_new_transaction = transaction is None
            if transaction is None:
                transaction = TransactionDto(
                    payment_id=data.payment_id,
                    user_id=autopayment.user_id,
                    status=TransactionStatus.PENDING,
                    purchase_type=autopayment.purchase_type,
                    gateway_type=PaymentGatewayType.PLATEGA,
                    gateway_display_name=autopayment.gateway_display_name,
                    payment_method=data.payment_method,
                    pricing=autopayment.pricing,
                    currency=autopayment.currency,
                    plan_snapshot=autopayment.plan_snapshot,
                )
                await self.transaction_dao.create(transaction)
                await self.uow.commit()

        if data.transaction_status == TransactionStatus.CANCELED:
            await self.process_payment.system(
                ProcessPaymentDto(
                    payment_id=data.payment_id,
                    new_transaction_status=TransactionStatus.CANCELED,
                    gateway_type=PaymentGatewayType.PLATEGA,
                )
            )
            async with self.uow:
                refreshed = await self.autopayment_dao.get_by_subscription_id(
                    data.subscription_id
                )
                if refreshed is not None:
                    was_failed = refreshed.status == PlategaAutopaymentStatus.PAYMENT_FAILED
                    refreshed.status = PlategaAutopaymentStatus.PAYMENT_FAILED
                    refreshed.next_charge_at = data.next_charge_at
                    refreshed.last_charge_at = datetime_now()
                    await self.autopayment_dao.update(refreshed)
                    await self.uow.commit()
                else:
                    was_failed = False

            if is_new_transaction or not was_failed:
                await self.event_publisher.publish(
                    AutopaymentFailedEvent(
                        user=user,
                        amount=autopayment.pricing.final_amount,
                        currency=autopayment.currency.symbol,
                    )
                )
            return

        if data.transaction_status == TransactionStatus.REFUNDED:
            await self.process_payment.system(
                ProcessPaymentDto(
                    payment_id=data.payment_id,
                    new_transaction_status=TransactionStatus.REFUNDED,
                    gateway_type=PaymentGatewayType.PLATEGA,
                )
            )
            return

        if data.transaction_status != TransactionStatus.COMPLETED:
            raise ValueError(
                f"Unsupported Platega autopayment transaction status: "
                f"'{data.transaction_status}'"
            )
        if data.next_charge_at is None:
            raise ValueError("Platega autopayment callback is missing NextChargeAt")

        expire_at = autopayment_expire_at(data.next_charge_at)
        await self.process_payment.system(
            ProcessPaymentDto(
                payment_id=data.payment_id,
                new_transaction_status=TransactionStatus.COMPLETED,
                gateway_type=PaymentGatewayType.PLATEGA,
                subscription_expire_at=expire_at,
            )
        )

        async with self.uow:
            transaction = await self.transaction_dao.get_by_payment_id(data.payment_id)
            if transaction is None or transaction.status != TransactionStatus.COMPLETED:
                raise RuntimeError(
                    f"Platega autopayment transaction '{data.payment_id}' was not completed"
                )

            autopayment = await self.autopayment_dao.get_by_subscription_id(
                data.subscription_id
            )
            if autopayment is None:
                raise RuntimeError(
                    f"Platega autopayment '{data.subscription_id}' disappeared during processing"
                )
            autopayment.status = PlategaAutopaymentStatus.ACTIVE
            autopayment.next_charge_at = data.next_charge_at
            autopayment.last_charge_at = datetime_now()
            autopayment.purchase_type = PurchaseType.RENEW
            await self.autopayment_dao.update(autopayment)
            await self.uow.commit()

        await self._cancel_older_autopayments(autopayment)

    async def _process_status_callback(
        self,
        autopayment: PlategaAutopaymentDto,
        data: ProcessPlategaAutopaymentDto,
    ) -> None:
        status = data.autopayment_status
        if status == "SUBSCRIPTION_ACTIVATED":
            autopayment.status = PlategaAutopaymentStatus.ACTIVE
            autopayment.next_charge_at = data.next_charge_at
        elif status in {"SUBSCRIPTION_CANCELED", "SUBSCRIPTION_CANCELLED"}:
            autopayment.status = PlategaAutopaymentStatus.CANCELED
            autopayment.next_charge_at = None
        else:
            logger.info(
                f"Ignoring unsupported Platega autopayment status '{status}' for "
                f"'{autopayment.subscription_id}'"
            )
            return
        await self.autopayment_dao.update(autopayment)

    @staticmethod
    def _validate_charge(
        expected_amount: Decimal,
        expected_currency: Currency,
        data: ProcessPlategaAutopaymentDto,
    ) -> None:
        if data.amount is None or data.currency is None:
            raise ValueError("Platega autopayment callback is missing Amount or Currency")
        if data.amount != expected_amount or data.currency != expected_currency:
            raise ValueError(
                f"Platega autopayment charge mismatch: expected "
                f"'{expected_amount} {expected_currency.value}', got "
                f"'{data.amount} {data.currency.value}'"
            )

    async def _cancel_older_autopayments(
        self, autopayment: PlategaAutopaymentDto
    ) -> None:
        if autopayment.created_at is None:
            return

        async with self.uow:
            older = await self.autopayment_dao.get_older_replaceable(
                autopayment.user_id, autopayment.created_at
            )

        if not older:
            return

        gateway = await self.get_payment_gateway_instance.system(PaymentGatewayType.PLATEGA)
        for previous in older:
            await gateway.handle_cancel_autopayment(previous.subscription_id)
            async with self.uow:
                current = await self.autopayment_dao.get_by_subscription_id(
                    previous.subscription_id
                )
                if current is not None:
                    current.status = PlategaAutopaymentStatus.REPLACED
                    current.next_charge_at = None
                    await self.autopayment_dao.update(current)
                    await self.uow.commit()
