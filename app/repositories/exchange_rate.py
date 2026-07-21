from __future__ import annotations

from collections.abc import Sequence
from datetime import datetime

from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.collectors.base import RatePoint
from app.models.exchange_rate import ExchangeRate

_CONFLICT_COLUMNS = ("source", "base_currency", "quote_currency", "observed_at")


class ExchangeRateRepository:
    """Sole owner of exchange_rates persistence.

    Works in RatePoint at its boundary in both directions — callers never see
    the SQLAlchemy model, so the ORM stays fully encapsulated here.
    """

    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def save(self, rate: RatePoint) -> None:
        await self.bulk_save([rate])

    async def bulk_save(self, rates: Sequence[RatePoint]) -> None:
        if not rates:
            return

        stmt = insert(ExchangeRate).values(
            [
                {
                    "source": rate.source,
                    "base_currency": rate.base_currency,
                    "quote_currency": rate.quote_currency,
                    "value": rate.value,
                    "observed_at": rate.observed_at,
                }
                for rate in rates
            ]
        )
        # Collection runs on a schedule and can be re-triggered for a day that
        # was already collected (e.g. CBA publishes once per Yerevan day) —
        # ON CONFLICT DO NOTHING makes repeated saves for the same
        # (source, base_currency, quote_currency, observed_at) a no-op
        # instead of a constraint-violation error.
        stmt = stmt.on_conflict_do_nothing(index_elements=list(_CONFLICT_COLUMNS))
        await self._session.execute(stmt)
        await self._session.commit()

    async def get_latest(
        self, source: str, base_currency: str, quote_currency: str
    ) -> RatePoint | None:
        stmt = (
            select(ExchangeRate)
            .where(
                ExchangeRate.source == source,
                ExchangeRate.base_currency == base_currency,
                ExchangeRate.quote_currency == quote_currency,
            )
            .order_by(ExchangeRate.observed_at.desc())
            .limit(1)
        )
        row = (await self._session.execute(stmt)).scalar_one_or_none()
        return self._to_rate_point(row) if row is not None else None

    async def find_by_date(
        self, source: str, base_currency: str, quote_currency: str, observed_at: datetime
    ) -> RatePoint | None:
        stmt = select(ExchangeRate).where(
            ExchangeRate.source == source,
            ExchangeRate.base_currency == base_currency,
            ExchangeRate.quote_currency == quote_currency,
            ExchangeRate.observed_at == observed_at,
        )
        row = (await self._session.execute(stmt)).scalar_one_or_none()
        return self._to_rate_point(row) if row is not None else None

    async def list_history(
        self,
        source: str,
        base_currency: str,
        quote_currency: str,
        *,
        since: datetime | None = None,
        until: datetime | None = None,
        limit: int = 100,
    ) -> list[RatePoint]:
        stmt = select(ExchangeRate).where(
            ExchangeRate.source == source,
            ExchangeRate.base_currency == base_currency,
            ExchangeRate.quote_currency == quote_currency,
        )
        if since is not None:
            stmt = stmt.where(ExchangeRate.observed_at >= since)
        if until is not None:
            stmt = stmt.where(ExchangeRate.observed_at <= until)
        stmt = stmt.order_by(ExchangeRate.observed_at.desc()).limit(limit)

        rows = (await self._session.execute(stmt)).scalars().all()
        return [self._to_rate_point(row) for row in rows]

    async def exists(
        self, source: str, base_currency: str, quote_currency: str, observed_at: datetime
    ) -> bool:
        stmt = (
            select(ExchangeRate.id)
            .where(
                ExchangeRate.source == source,
                ExchangeRate.base_currency == base_currency,
                ExchangeRate.quote_currency == quote_currency,
                ExchangeRate.observed_at == observed_at,
            )
            .limit(1)
        )
        return (await self._session.execute(stmt)).scalar_one_or_none() is not None

    @staticmethod
    def _to_rate_point(row: ExchangeRate) -> RatePoint:
        return RatePoint(
            source=row.source,
            base_currency=row.base_currency,
            quote_currency=row.quote_currency,
            value=row.value,
            observed_at=row.observed_at,
        )
