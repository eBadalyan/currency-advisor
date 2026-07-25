from __future__ import annotations

from datetime import UTC, datetime, timedelta
from decimal import Decimal

from sqlalchemy.ext.asyncio import AsyncSession

from app.collectors.base import RatePoint
from app.repositories.exchange_rate import ExchangeRateRepository
from app.repositories.notification_state_repository import NotificationStateRepository
from app.services.bank_average_service import SOURCE_NAME as BANK_AVERAGE_SOURCE_NAME
from app.services.rise_alert_service import RiseAlertService

_START = datetime(2026, 7, 25, 8, 0, tzinfo=UTC)


def _bank_average_point(value: str, minutes_after_start: int) -> RatePoint:
    return RatePoint(
        BANK_AVERAGE_SOURCE_NAME,
        "RUB",
        "AMD",
        Decimal(value),
        _START + timedelta(minutes=minutes_after_start),
    )


def _service(db_session: AsyncSession, *, streak_threshold: int = 2) -> RiseAlertService:
    return RiseAlertService(
        ExchangeRateRepository(db_session),
        NotificationStateRepository(db_session),
        streak_threshold,
    )


async def test_no_data_returns_no_alert(db_session: AsyncSession) -> None:
    service = _service(db_session)

    assert await service.check() is None


async def test_first_ever_check_bootstraps_state_without_alerting(
    db_session: AsyncSession,
) -> None:
    await ExchangeRateRepository(db_session).save(_bank_average_point("3.72", 0))
    service = _service(db_session)

    assert await service.check() is None


async def test_repeated_identical_value_does_not_alert(db_session: AsyncSession) -> None:
    repository = ExchangeRateRepository(db_session)
    service = _service(db_session)
    await repository.save(_bank_average_point("3.72", 0))
    await service.check()

    await repository.save(_bank_average_point("3.72", 30))

    assert await service.check() is None


async def test_single_rise_below_threshold_does_not_alert(db_session: AsyncSession) -> None:
    repository = ExchangeRateRepository(db_session)
    service = _service(db_session, streak_threshold=2)
    await repository.save(_bank_average_point("3.72", 0))
    await service.check()

    await repository.save(_bank_average_point("3.74", 30))

    assert await service.check() is None


async def test_two_consecutive_rises_triggers_alert(db_session: AsyncSession) -> None:
    repository = ExchangeRateRepository(db_session)
    service = _service(db_session, streak_threshold=2)
    await repository.save(_bank_average_point("3.72", 0))
    await service.check()
    await repository.save(_bank_average_point("3.74", 30))
    await service.check()

    await repository.save(_bank_average_point("3.76", 60))
    alert = await service.check()

    assert alert is not None
    assert alert.current_value == Decimal("3.76")
    assert alert.streak_length == 2
    assert alert.previous_alerted_value is None


async def test_repeated_value_at_already_alerted_high_does_not_realert(
    db_session: AsyncSession,
) -> None:
    repository = ExchangeRateRepository(db_session)
    service = _service(db_session, streak_threshold=2)
    for value, minutes in (("3.72", 0), ("3.74", 30), ("3.76", 60)):
        await repository.save(_bank_average_point(value, minutes))
        await service.check()

    await repository.save(_bank_average_point("3.76", 90))

    assert await service.check() is None


async def test_any_further_rise_after_arming_realerts_immediately(
    db_session: AsyncSession,
) -> None:
    repository = ExchangeRateRepository(db_session)
    service = _service(db_session, streak_threshold=2)
    for value, minutes in (("3.72", 0), ("3.74", 30), ("3.76", 60)):
        await repository.save(_bank_average_point(value, minutes))
        await service.check()

    await repository.save(_bank_average_point("3.77", 90))
    alert = await service.check()

    assert alert is not None
    assert alert.current_value == Decimal("3.77")
    assert alert.previous_alerted_value == Decimal("3.76")


async def test_rate_decrease_resets_streak_and_rearms_alerting(db_session: AsyncSession) -> None:
    repository = ExchangeRateRepository(db_session)
    service = _service(db_session, streak_threshold=2)
    for value, minutes in (("3.72", 0), ("3.74", 30), ("3.76", 60)):
        await repository.save(_bank_average_point(value, minutes))
        await service.check()

    await repository.save(_bank_average_point("3.68", 90))
    assert await service.check() is None  # decrease: streak resets, no alert

    await repository.save(_bank_average_point("3.70", 120))
    assert await service.check() is None  # first rise after reset: streak=1

    await repository.save(_bank_average_point("3.72", 150))
    alert = await service.check()

    assert alert is not None
    assert alert.current_value == Decimal("3.72")
    assert alert.streak_length == 2
    assert alert.previous_alerted_value is None
