from datetime import datetime
from typing import Optional, cast
from uuid import UUID

from adaptix import Retort
from adaptix.conversion import ConversionRetort
from loguru import logger
from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from src.application.common.dao import PlategaAutopaymentDao
from src.application.dto import PlategaAutopaymentDto
from src.core.enums import PlategaAutopaymentStatus
from src.infrastructure.database.models import PlategaAutopayment

from .base import BaseDaoImpl

_REPLACEABLE_STATUSES = (
    PlategaAutopaymentStatus.PENDING,
    PlategaAutopaymentStatus.ACTIVE,
    PlategaAutopaymentStatus.PAYMENT_FAILED,
)


class PlategaAutopaymentDaoImpl(PlategaAutopaymentDao, BaseDaoImpl):
    def __init__(
        self,
        session: AsyncSession,
        retort: Retort,
        conversion_retort: ConversionRetort,
    ) -> None:
        self.session = session
        self.retort = retort
        self.conversion_retort = conversion_retort
        self._convert_to_dto = conversion_retort.get_converter(
            PlategaAutopayment, PlategaAutopaymentDto
        )
        self._convert_to_dto_list = conversion_retort.get_converter(
            list[PlategaAutopayment], list[PlategaAutopaymentDto]
        )

    async def create(self, autopayment: PlategaAutopaymentDto) -> PlategaAutopaymentDto:
        data = self._serialize_for_update(
            autopayment.as_fully_changed(), PlategaAutopaymentDto, PlategaAutopayment
        )
        data.pop("id", None)
        data.pop("created_at", None)
        data.pop("updated_at", None)
        db_autopayment = PlategaAutopayment(**data)
        self.session.add(db_autopayment)
        await self.session.flush()
        logger.debug(f"Created Platega autopayment '{autopayment.subscription_id}'")
        return self._convert_to_dto(db_autopayment)

    async def update(
        self, autopayment: PlategaAutopaymentDto
    ) -> Optional[PlategaAutopaymentDto]:
        if not autopayment.changed_data:
            return autopayment

        values = self._serialize_for_update(
            autopayment, PlategaAutopaymentDto, PlategaAutopayment
        )
        stmt = (
            update(PlategaAutopayment)
            .where(PlategaAutopayment.subscription_id == autopayment.subscription_id)
            .values(**values)
            .returning(PlategaAutopayment)
        )
        db_autopayment = await self.session.scalar(stmt)
        return self._convert_to_dto(db_autopayment) if db_autopayment else None

    async def get_by_subscription_id(
        self, subscription_id: UUID
    ) -> Optional[PlategaAutopaymentDto]:
        stmt = select(PlategaAutopayment).where(
            PlategaAutopayment.subscription_id == subscription_id
        )
        db_autopayment = await self.session.scalar(stmt)
        return self._convert_to_dto(db_autopayment) if db_autopayment else None

    async def get_latest_replaceable(self, user_id: int) -> Optional[PlategaAutopaymentDto]:
        stmt = (
            select(PlategaAutopayment)
            .where(
                PlategaAutopayment.user_id == user_id,
                PlategaAutopayment.status.in_(_REPLACEABLE_STATUSES),
            )
            .order_by(PlategaAutopayment.created_at.desc())
            .limit(1)
        )
        db_autopayment = await self.session.scalar(stmt)
        return self._convert_to_dto(db_autopayment) if db_autopayment else None

    async def get_older_replaceable(
        self, user_id: int, created_before: datetime
    ) -> list[PlategaAutopaymentDto]:
        stmt = select(PlategaAutopayment).where(
            PlategaAutopayment.user_id == user_id,
            PlategaAutopayment.created_at < created_before,
            PlategaAutopayment.status.in_(_REPLACEABLE_STATUSES),
        )
        result = await self.session.scalars(stmt)
        rows = cast(list[PlategaAutopayment], result.all())
        return self._convert_to_dto_list(rows)
