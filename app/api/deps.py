from __future__ import annotations

from fastapi import Depends
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.session import get_session
from app.repositories.exchange_rate import ExchangeRateRepository


async def get_exchange_rate_repository(
    session: AsyncSession = Depends(get_session),
) -> ExchangeRateRepository:
    return ExchangeRateRepository(session)
