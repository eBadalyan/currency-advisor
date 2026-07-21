from __future__ import annotations

from datetime import UTC, datetime
from decimal import Decimal

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.collectors.base import RateCollector, RatePoint
from app.models.exchange_rate import ExchangeRate
from app.repositories.exchange_rate import ExchangeRateRepository
from app.services.rate_service import RateService

_SOURCE = "Fake Source"
_OBSERVED_AT = datetime(2026, 7, 19, 20, 0, tzinfo=UTC)


class _FakeCollector(RateCollector):
    def __init__(self, points: list[RatePoint]) -> None:
        self._points = points
        self.call_count = 0

    async def collect(self) -> list[RatePoint]:
        self.call_count += 1
        return self._points


def _rate_point(base: str, value: str) -> RatePoint:
    return RatePoint(
        source=_SOURCE,
        base_currency=base,
        quote_currency="AMD",
        value=Decimal(value),
        observed_at=_OBSERVED_AT,
    )


async def test_collect_and_save_persists_and_returns_points(db_session: AsyncSession) -> None:
    collector = _FakeCollector([_rate_point("RUB", "4.6"), _rate_point("USD", "384.5")])
    service = RateService(ExchangeRateRepository(db_session))

    returned = await service.collect_and_save(collector)

    assert returned == [_rate_point("RUB", "4.6"), _rate_point("USD", "384.5")]

    repository = ExchangeRateRepository(db_session)
    assert await repository.get_latest(_SOURCE, "RUB", "AMD") == _rate_point("RUB", "4.6")
    assert await repository.get_latest(_SOURCE, "USD", "AMD") == _rate_point("USD", "384.5")


async def test_collect_and_save_is_idempotent_when_rerun(db_session: AsyncSession) -> None:
    collector = _FakeCollector([_rate_point("RUB", "4.6")])
    service = RateService(ExchangeRateRepository(db_session))

    await service.collect_and_save(collector)
    await service.collect_and_save(collector)

    assert collector.call_count == 2
    row_count = (
        await db_session.execute(select(func.count()).select_from(ExchangeRate))
    ).scalar_one()
    assert row_count == 1
    latest = await ExchangeRateRepository(db_session).get_latest(_SOURCE, "RUB", "AMD")
    assert latest == _rate_point("RUB", "4.6")


async def test_get_latest_rate_returns_point(db_session: AsyncSession) -> None:
    repository = ExchangeRateRepository(db_session)
    await repository.save(_rate_point("RUB", "4.6"))
    service = RateService(repository)

    assert await service.get_latest_rate(_SOURCE, "RUB", "AMD") == _rate_point("RUB", "4.6")


async def test_get_latest_rate_returns_none_when_no_data(db_session: AsyncSession) -> None:
    service = RateService(ExchangeRateRepository(db_session))

    assert await service.get_latest_rate(_SOURCE, "RUB", "AMD") is None
