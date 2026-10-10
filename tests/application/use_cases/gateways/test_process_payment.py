from types import SimpleNamespace
from unittest.mock import AsyncMock
from uuid import uuid4

import pytest

from src.application.use_cases.gateways.commands.payment import ProcessPayment, ProcessPaymentDto
from src.core.enums import PaymentGatewayType, TransactionStatus


class FakeUnitOfWork:
    commit = AsyncMock()

    async def __aenter__(self) -> "FakeUnitOfWork":
        return self

    async def __aexit__(self, *args: object) -> None:
        return None


@pytest.mark.asyncio
async def test_completed_payment_can_transition_from_canceled() -> None:
    payment_id = uuid4()
    transaction = SimpleNamespace(
        payment_id=payment_id,
        user_id=42,
        gateway_type=PaymentGatewayType.PLATEGA,
    )
    user = SimpleNamespace(remna_name="rs_42", log="user:42")
    transaction_dao = AsyncMock()
    transaction_dao.get_by_payment_id.return_value = transaction
    transaction_dao.transition_status.return_value = transaction
    user_dao = AsyncMock()
    user_dao.get_by_id.return_value = user

    processor = ProcessPayment(
        uow=FakeUnitOfWork(),  # type: ignore[arg-type]
        user_dao=user_dao,
        transaction_dao=transaction_dao,
        subscription_dao=AsyncMock(),
        referral_dao=AsyncMock(),
        event_publisher=AsyncMock(),
        notifier=AsyncMock(),
        redirect=AsyncMock(),
        assign_referral_rewards=AsyncMock(),
        purchase_subscription=AsyncMock(),
    )
    processor._handle_success = AsyncMock()  # type: ignore[method-assign]

    await processor.system(
        ProcessPaymentDto(
            payment_id=payment_id,
            new_transaction_status=TransactionStatus.COMPLETED,
            gateway_type=PaymentGatewayType.PLATEGA,
        )
    )

    transaction_dao.transition_status.assert_awaited_once_with(
        payment_id,
        TransactionStatus.COMPLETED,
        (
            TransactionStatus.PENDING,
            TransactionStatus.FAILED,
            TransactionStatus.CANCELED,
        ),
    )
    processor._handle_success.assert_awaited_once_with(user, transaction)


@pytest.mark.asyncio
async def test_duplicate_completed_payment_is_not_processed_twice() -> None:
    payment_id = uuid4()
    transaction = SimpleNamespace(
        payment_id=payment_id,
        user_id=42,
        gateway_type=PaymentGatewayType.PLATEGA,
    )
    user = SimpleNamespace(remna_name="rs_42", log="user:42")
    transaction_dao = AsyncMock()
    transaction_dao.get_by_payment_id.return_value = transaction
    transaction_dao.transition_status.return_value = None
    user_dao = AsyncMock()
    user_dao.get_by_id.return_value = user

    processor = ProcessPayment(
        uow=FakeUnitOfWork(),  # type: ignore[arg-type]
        user_dao=user_dao,
        transaction_dao=transaction_dao,
        subscription_dao=AsyncMock(),
        referral_dao=AsyncMock(),
        event_publisher=AsyncMock(),
        notifier=AsyncMock(),
        redirect=AsyncMock(),
        assign_referral_rewards=AsyncMock(),
        purchase_subscription=AsyncMock(),
    )
    processor._handle_success = AsyncMock()  # type: ignore[method-assign]

    await processor.system(
        ProcessPaymentDto(
            payment_id=payment_id,
            new_transaction_status=TransactionStatus.COMPLETED,
            gateway_type=PaymentGatewayType.PLATEGA,
        )
    )

    processor._handle_success.assert_not_awaited()
