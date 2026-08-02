from datetime import datetime
from typing import Optional, Protocol, runtime_checkable
from uuid import UUID

from src.application.dto import PlategaAutopaymentDto


@runtime_checkable
class PlategaAutopaymentDao(Protocol):
    async def create(self, autopayment: PlategaAutopaymentDto) -> PlategaAutopaymentDto: ...

    async def update(
        self, autopayment: PlategaAutopaymentDto
    ) -> Optional[PlategaAutopaymentDto]: ...

    async def get_by_subscription_id(
        self, subscription_id: UUID
    ) -> Optional[PlategaAutopaymentDto]: ...

    async def get_latest_replaceable(self, user_id: int) -> Optional[PlategaAutopaymentDto]: ...

    async def get_older_replaceable(
        self, user_id: int, created_before: datetime
    ) -> list[PlategaAutopaymentDto]: ...
