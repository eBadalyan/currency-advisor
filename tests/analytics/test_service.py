from __future__ import annotations

from datetime import UTC, datetime, timedelta
from decimal import Decimal

from sqlalchemy.ext.asyncio import AsyncSession

from app.analytics.service import AnalyticsService
from app.collectors.base import RatePoint
from app.repositories.exchange_rate import ExchangeRateRepository

_SOURCE = "Central Bank of Armenia"
_START = datetime(2026, 7, 1, 20, 0, tzinfo=UTC)


def _point(value: str, day_offset: int) -> RatePoint:
    return RatePoint(_SOURCE, "RUB", "AMD", Decimal(value), _START + timedelta(days=day_offset))


async def test_get_indicators_computes_from_persisted_history(db_session: AsyncSession) -> None:
    repository = ExchangeRateRepository(db_session)
    await repository.bulk_save([_point("4.0", 0), _point("4.2", 1), _point("4.4", 2)])
    service = AnalyticsService(repository)

    indicators = await service.get_indicators(_SOURCE, "RUB", "AMD", window=3)

    assert indicators.sample_size == 3
    assert indicators.moving_average == Decimal("4.2")
    assert indicators.rate_change_percent == Decimal("10")
    assert indicators.minimum is not None
    assert indicators.minimum.value == Decimal("4.0")
    assert indicators.maximum is not None
    assert indicators.maximum.value == Decimal("4.4")


async def test_get_indicators_respects_window_limit(db_session: AsyncSession) -> None:
    repository = ExchangeRateRepository(db_session)
    await repository.bulk_save(
        [_point(str(Decimal("4.0") + Decimal(i) / 10), i) for i in range(10)]
    )
    service = AnalyticsService(repository)

    indicators = await service.get_indicators(_SOURCE, "RUB", "AMD", window=3)

    assert indicators.sample_size == 3


async def test_get_indicators_returns_empty_result_with_no_data(db_session: AsyncSession) -> None:
    service = AnalyticsService(ExchangeRateRepository(db_session))

    indicators = await service.get_indicators(_SOURCE, "RUB", "AMD")

    assert indicators.sample_size == 0
    assert indicators.moving_average is None
    assert indicators.rate_change_percent is None
    assert indicators.volatility is None
    assert indicators.minimum is None
    assert indicators.maximum is None
