from __future__ import annotations

from datetime import UTC, datetime
from decimal import Decimal

from sqlalchemy.ext.asyncio import AsyncSession

from app.collectors.acba_bank import SOURCE_NAME as ACBA_BANK_SOURCE_NAME
from app.collectors.ameriabank import SOURCE_NAME as AMERIABANK_SOURCE_NAME
from app.collectors.base import RatePoint
from app.collectors.evocabank import SOURCE_NAME as EVOCABANK_SOURCE_NAME
from app.collectors.vtb_am import SOURCE_NAME as VTB_AM_SOURCE_NAME
from app.repositories.exchange_rate import ExchangeRateRepository
from app.services.bank_average_service import SOURCE_NAME, BankAverageService

_OBSERVED_AT = datetime(2026, 7, 21, tzinfo=UTC)


def _bank_rate(source: str, value: str) -> RatePoint:
    return RatePoint(source, "RUB", "AMD", Decimal(value), _OBSERVED_AT)


async def test_computes_median_of_four_reporting_banks(db_session: AsyncSession) -> None:
    repository = ExchangeRateRepository(db_session)
    await repository.bulk_save(
        [
            _bank_rate(AMERIABANK_SOURCE_NAME, "3.72"),
            _bank_rate(EVOCABANK_SOURCE_NAME, "3.80"),
            _bank_rate(ACBA_BANK_SOURCE_NAME, "3.73"),
            _bank_rate(VTB_AM_SOURCE_NAME, "3.75"),
        ]
    )
    service = BankAverageService(repository)

    point = await service.compute_and_save()

    assert point is not None
    assert point.source == SOURCE_NAME
    assert point.base_currency == "RUB"
    assert point.quote_currency == "AMD"
    # sorted: 3.72, 3.73, 3.75, 3.80 -> median of middle two = 3.74
    assert point.value == Decimal("3.74")
    assert await repository.exists(SOURCE_NAME, "RUB", "AMD", point.observed_at)


async def test_median_resists_a_single_outlier(db_session: AsyncSession) -> None:
    repository = ExchangeRateRepository(db_session)
    await repository.bulk_save(
        [
            _bank_rate(AMERIABANK_SOURCE_NAME, "3.72"),
            _bank_rate(EVOCABANK_SOURCE_NAME, "3.73"),
            _bank_rate(ACBA_BANK_SOURCE_NAME, "3.75"),
            # wildly off relative to the other three
            _bank_rate(VTB_AM_SOURCE_NAME, "10.00"),
        ]
    )
    service = BankAverageService(repository)

    point = await service.compute_and_save()

    assert point is not None
    # median of 3.72/3.73/3.75/10.00 = (3.73+3.75)/2 = 3.74, well away from
    # the outlier — an unweighted mean would have been pulled toward ~5.3.
    assert point.value == Decimal("3.74")


async def test_below_quorum_does_not_save(db_session: AsyncSession) -> None:
    repository = ExchangeRateRepository(db_session)
    await repository.save(_bank_rate(AMERIABANK_SOURCE_NAME, "3.72"))
    service = BankAverageService(repository)

    point = await service.compute_and_save()

    assert point is None
    assert not await repository.exists(SOURCE_NAME, "RUB", "AMD", _OBSERVED_AT)


async def test_no_banks_reporting_does_not_save(db_session: AsyncSession) -> None:
    service = BankAverageService(ExchangeRateRepository(db_session))

    point = await service.compute_and_save()

    assert point is None


async def test_exactly_at_quorum_saves(db_session: AsyncSession) -> None:
    repository = ExchangeRateRepository(db_session)
    await repository.bulk_save(
        [
            _bank_rate(AMERIABANK_SOURCE_NAME, "3.72"),
            _bank_rate(EVOCABANK_SOURCE_NAME, "3.80"),
        ]
    )
    service = BankAverageService(repository)

    point = await service.compute_and_save()

    assert point is not None
    assert point.value == Decimal("3.76")
