from __future__ import annotations

from datetime import UTC, datetime, timedelta
from decimal import Decimal

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from app.collectors.acba_bank import SOURCE_NAME as ACBA_BANK_SOURCE_NAME
from app.collectors.ameriabank import SOURCE_NAME as AMERIABANK_SOURCE_NAME
from app.collectors.base import RatePoint
from app.collectors.cba import SOURCE_NAME as CBA_SOURCE_NAME
from app.repositories.exchange_rate import ExchangeRateRepository
from app.services.collector_health_service import CollectorHealthService

_NOW = datetime(2026, 7, 21, 12, 0, tzinfo=UTC)


@pytest.fixture(autouse=True)
def _frozen_now(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("app.services.collector_health_service._utcnow", lambda: _NOW)


def _point(source: str, base: str, quote: str, hours_ago: float) -> RatePoint:
    return RatePoint(source, base, quote, Decimal("4.6"), _NOW - timedelta(hours=hours_ago))


async def test_never_collected_source_is_stale_with_no_timestamp(
    db_session: AsyncSession,
) -> None:
    service = CollectorHealthService(ExchangeRateRepository(db_session))

    statuses = await service.get_status()

    cba_rub_amd = next(
        s for s in statuses if s.source == CBA_SOURCE_NAME and s.base_currency == "RUB"
    )
    assert cba_rub_amd.last_observed_at is None
    assert cba_rub_amd.is_stale is True


async def test_daily_source_within_two_days_is_not_stale(db_session: AsyncSession) -> None:
    repository = ExchangeRateRepository(db_session)
    # CBA's observed_at is calendar-day-pinned, not collection-time-pinned —
    # 20 hours "old" is normal on a healthy day, must not be flagged.
    await repository.save(_point(CBA_SOURCE_NAME, "RUB", "AMD", hours_ago=20))
    service = CollectorHealthService(repository)

    statuses = await service.get_status()

    cba_rub_amd = next(
        s for s in statuses if s.source == CBA_SOURCE_NAME and s.base_currency == "RUB"
    )
    assert cba_rub_amd.is_stale is False


async def test_daily_source_past_two_days_is_stale(db_session: AsyncSession) -> None:
    repository = ExchangeRateRepository(db_session)
    await repository.save(_point(CBA_SOURCE_NAME, "RUB", "AMD", hours_ago=49))
    service = CollectorHealthService(repository)

    statuses = await service.get_status()

    cba_rub_amd = next(
        s for s in statuses if s.source == CBA_SOURCE_NAME and s.base_currency == "RUB"
    )
    assert cba_rub_amd.is_stale is True


async def test_intraday_source_within_its_interval_multiple_is_not_stale(
    db_session: AsyncSession,
) -> None:
    repository = ExchangeRateRepository(db_session)
    # Ameriabank's interval is 30 min; 3x that is 90 min = 1.5h.
    await repository.save(_point(AMERIABANK_SOURCE_NAME, "RUB", "AMD", hours_ago=1))
    service = CollectorHealthService(repository)

    statuses = await service.get_status()

    ameriabank = next(s for s in statuses if s.source == AMERIABANK_SOURCE_NAME)
    assert ameriabank.is_stale is False


async def test_intraday_source_past_its_interval_multiple_is_stale(
    db_session: AsyncSession,
) -> None:
    repository = ExchangeRateRepository(db_session)
    await repository.save(_point(ACBA_BANK_SOURCE_NAME, "RUB", "AMD", hours_ago=2))
    service = CollectorHealthService(repository)

    statuses = await service.get_status()

    acba = next(s for s in statuses if s.source == ACBA_BANK_SOURCE_NAME)
    assert acba.is_stale is True


async def test_get_status_covers_every_tracked_source(db_session: AsyncSession) -> None:
    service = CollectorHealthService(ExchangeRateRepository(db_session))

    statuses = await service.get_status()

    # CBA (RUB/AMD + USD/AMD) + CBR + 4 banks + Bank Average = 8 entries.
    assert len(statuses) == 8
