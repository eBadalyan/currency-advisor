from __future__ import annotations

from datetime import UTC, datetime, timedelta
from decimal import Decimal

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from app.analytics.service import AnalyticsService
from app.collectors.base import RatePoint
from app.repositories.exchange_rate import ExchangeRateRepository

_SOURCE = "Central Bank of Armenia"
_NOW = datetime(2026, 7, 21, 12, 0, tzinfo=UTC)


def _point(value: str, days_ago: int) -> RatePoint:
    return RatePoint(_SOURCE, "RUB", "AMD", Decimal(value), _NOW - timedelta(days=days_ago))


@pytest.fixture(autouse=True)
def _frozen_now(monkeypatch: pytest.MonkeyPatch) -> None:
    # get_indicators' window is calendar-day-relative to "now" — freeze it so
    # fixture dates stay meaningful regardless of which day the suite runs.
    monkeypatch.setattr("app.analytics.service._utcnow", lambda: _NOW)


async def test_get_indicators_computes_from_persisted_history(db_session: AsyncSession) -> None:
    repository = ExchangeRateRepository(db_session)
    await repository.bulk_save([_point("4.0", 2), _point("4.2", 1), _point("4.4", 0)])
    service = AnalyticsService(repository)

    indicators = await service.get_indicators(_SOURCE, "RUB", "AMD", window_days=3)

    assert indicators.sample_size == 3
    assert indicators.moving_average == Decimal("4.2")
    assert indicators.rate_change_percent == Decimal("10")
    assert indicators.minimum is not None
    assert indicators.minimum.value == Decimal("4.0")
    assert indicators.maximum is not None
    assert indicators.maximum.value == Decimal("4.4")


async def test_get_indicators_excludes_points_older_than_the_window(
    db_session: AsyncSession,
) -> None:
    repository = ExchangeRateRepository(db_session)
    await repository.bulk_save(
        [_point("3.0", 40), _point("4.0", 10), _point("4.2", 5), _point("4.4", 0)]
    )
    service = AnalyticsService(repository)

    indicators = await service.get_indicators(_SOURCE, "RUB", "AMD", window_days=30)

    # The 40-days-ago point falls outside a 30-day window and must not
    # contribute to sample size, average, or min/max — this is the exact
    # row-count-vs-date-range distinction the window fix addresses.
    assert indicators.sample_size == 3
    assert indicators.minimum is not None
    assert indicators.minimum.value == Decimal("4.0")


async def test_get_indicators_handles_dense_sub_daily_history(db_session: AsyncSession) -> None:
    # Mirrors the bank sources, which write on the scheduler's own interval
    # rather than once/day: many points on the same day must all still count
    # toward the window, not get truncated by a row-count limit.
    repository = ExchangeRateRepository(db_session)
    points = [
        RatePoint(_SOURCE, "RUB", "AMD", Decimal("4.0"), _NOW - timedelta(minutes=30 * i))
        for i in range(200)
    ]
    await repository.bulk_save(points)
    service = AnalyticsService(repository)

    indicators = await service.get_indicators(_SOURCE, "RUB", "AMD", window_days=1)

    # i=0..48 fall within the last 24h inclusive (30 min apart, i=48 lands
    # exactly on the `since` boundary); i=49..199 are older and excluded.
    assert indicators.sample_size == 49


async def test_get_indicators_returns_empty_result_with_no_data(db_session: AsyncSession) -> None:
    service = AnalyticsService(ExchangeRateRepository(db_session))

    indicators = await service.get_indicators(_SOURCE, "RUB", "AMD")

    assert indicators.sample_size == 0
    assert indicators.moving_average is None
    assert indicators.rate_change_percent is None
    assert indicators.volatility is None
    assert indicators.minimum is None
    assert indicators.maximum is None
