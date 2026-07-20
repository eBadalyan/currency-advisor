from __future__ import annotations

from datetime import UTC, datetime, timedelta
from decimal import Decimal

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.collectors.base import RatePoint
from app.models.exchange_rate import ExchangeRate
from app.repositories.exchange_rate import ExchangeRateRepository

_SOURCE = "Central Bank of Armenia"
_OBSERVED_AT = datetime(2026, 7, 19, 20, 0, tzinfo=UTC)


def _rate_point(
    *,
    base: str = "RUB",
    value: Decimal = Decimal("4.6"),
    observed_at: datetime = _OBSERVED_AT,
) -> RatePoint:
    return RatePoint(
        source=_SOURCE,
        base_currency=base,
        quote_currency="AMD",
        value=value,
        observed_at=observed_at,
    )


async def _row_count(session: AsyncSession) -> int:
    result = await session.execute(select(func.count()).select_from(ExchangeRate))
    return result.scalar_one()


async def test_save_persists_and_get_latest_returns_it(db_session: AsyncSession) -> None:
    repo = ExchangeRateRepository(db_session)
    point = _rate_point()

    await repo.save(point)
    latest = await repo.get_latest(_SOURCE, "RUB", "AMD")

    assert latest == point


async def test_save_is_idempotent_on_conflict(db_session: AsyncSession) -> None:
    repo = ExchangeRateRepository(db_session)
    point = _rate_point()

    await repo.save(point)
    await repo.save(point)

    assert await _row_count(db_session) == 1


async def test_bulk_save_dedupes_against_existing_and_within_batch(
    db_session: AsyncSession,
) -> None:
    repo = ExchangeRateRepository(db_session)
    point = _rate_point()
    await repo.save(point)

    await repo.bulk_save([point, point, _rate_point(base="USD", value=Decimal("384.5"))])

    assert await _row_count(db_session) == 2


async def test_get_latest_returns_most_recent_observed_at(db_session: AsyncSession) -> None:
    repo = ExchangeRateRepository(db_session)
    older = _rate_point(observed_at=_OBSERVED_AT - timedelta(days=1), value=Decimal("4.5"))
    newer = _rate_point(observed_at=_OBSERVED_AT, value=Decimal("4.6"))
    await repo.bulk_save([older, newer])

    latest = await repo.get_latest(_SOURCE, "RUB", "AMD")

    assert latest == newer


async def test_get_latest_returns_none_when_no_data(db_session: AsyncSession) -> None:
    repo = ExchangeRateRepository(db_session)

    assert await repo.get_latest(_SOURCE, "RUB", "AMD") is None


async def test_exists_true_and_false(db_session: AsyncSession) -> None:
    repo = ExchangeRateRepository(db_session)
    point = _rate_point()
    await repo.save(point)

    assert await repo.exists(_SOURCE, "RUB", "AMD", _OBSERVED_AT) is True
    assert await repo.exists(_SOURCE, "USD", "AMD", _OBSERVED_AT) is False


async def test_find_by_date_exact_match_and_none(db_session: AsyncSession) -> None:
    repo = ExchangeRateRepository(db_session)
    point = _rate_point()
    await repo.save(point)

    found = await repo.find_by_date(_SOURCE, "RUB", "AMD", _OBSERVED_AT)
    missing = await repo.find_by_date(_SOURCE, "RUB", "AMD", _OBSERVED_AT - timedelta(days=1))

    assert found == point
    assert missing is None
