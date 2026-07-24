from __future__ import annotations

from datetime import UTC, datetime, timedelta
from decimal import Decimal

from sqlalchemy.ext.asyncio import AsyncSession

from app.collectors.base import RatePoint
from app.repositories.exchange_rate import ExchangeRateRepository
from app.repositories.notification_state_repository import NotificationStateRepository
from app.services.bank_average_service import SOURCE_NAME as BANK_AVERAGE_SOURCE_NAME
from app.services.decline_alert_service import DeclineAlertService

_START = datetime(2026, 7, 24, 8, 0, tzinfo=UTC)


def _bank_average_point(value: str, minutes_after_start: int) -> RatePoint:
    return RatePoint(
        BANK_AVERAGE_SOURCE_NAME,
        "RUB",
        "AMD",
        Decimal(value),
        _START + timedelta(minutes=minutes_after_start),
    )


def _service(db_session: AsyncSession, *, streak_threshold: int = 2) -> DeclineAlertService:
    return DeclineAlertService(
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
    await ExchangeRateRepository(db_session).save(_bank_average_point("3.76", 0))
    service = _service(db_session)

    assert await service.check() is None


async def test_repeated_identical_value_does_not_alert(db_session: AsyncSession) -> None:
    repository = ExchangeRateRepository(db_session)
    service = _service(db_session)
    await repository.save(_bank_average_point("3.76", 0))
    await service.check()

    await repository.save(_bank_average_point("3.76", 30))

    assert await service.check() is None


async def test_single_drop_below_threshold_does_not_alert(db_session: AsyncSession) -> None:
    repository = ExchangeRateRepository(db_session)
    service = _service(db_session, streak_threshold=2)
    await repository.save(_bank_average_point("3.76", 0))
    await service.check()

    await repository.save(_bank_average_point("3.74", 30))

    assert await service.check() is None


async def test_two_consecutive_drops_triggers_alert(db_session: AsyncSession) -> None:
    repository = ExchangeRateRepository(db_session)
    service = _service(db_session, streak_threshold=2)
    await repository.save(_bank_average_point("3.76", 0))
    await service.check()
    await repository.save(_bank_average_point("3.74", 30))
    await service.check()

    await repository.save(_bank_average_point("3.72", 60))
    alert = await service.check()

    assert alert is not None
    assert alert.current_value == Decimal("3.72")
    assert alert.streak_length == 2
    assert alert.previous_alerted_value is None


async def test_repeated_value_at_already_alerted_low_does_not_realert(
    db_session: AsyncSession,
) -> None:
    repository = ExchangeRateRepository(db_session)
    service = _service(db_session, streak_threshold=2)
    for value, minutes in (("3.76", 0), ("3.74", 30), ("3.72", 60)):
        await repository.save(_bank_average_point(value, minutes))
        await service.check()

    await repository.save(_bank_average_point("3.72", 90))

    assert await service.check() is None


async def test_any_further_drop_after_arming_realerts_immediately(
    db_session: AsyncSession,
) -> None:
    # Once a decline streak has crossed the threshold and produced one
    # alert, streak_length only keeps growing (it doesn't reset except on a
    # rise) — so any further genuinely lower reading re-alerts right away,
    # rather than requiring a fresh N-in-a-row streak beneath the new floor.
    # This matches "alert on every new, deeper low relative to the last
    # alert" from the design spec, not "alert on every Nth step".
    repository = ExchangeRateRepository(db_session)
    service = _service(db_session, streak_threshold=2)
    for value, minutes in (("3.76", 0), ("3.74", 30), ("3.72", 60)):
        await repository.save(_bank_average_point(value, minutes))
        await service.check()

    await repository.save(_bank_average_point("3.71", 90))
    alert = await service.check()

    assert alert is not None
    assert alert.current_value == Decimal("3.71")
    assert alert.previous_alerted_value == Decimal("3.72")


async def test_rate_increase_resets_streak_and_rearms_alerting(db_session: AsyncSession) -> None:
    repository = ExchangeRateRepository(db_session)
    service = _service(db_session, streak_threshold=2)
    for value, minutes in (("3.76", 0), ("3.74", 30), ("3.72", 60)):
        await repository.save(_bank_average_point(value, minutes))
        await service.check()

    await repository.save(_bank_average_point("3.80", 90))
    assert await service.check() is None  # increase: streak resets, no alert

    await repository.save(_bank_average_point("3.78", 120))
    assert await service.check() is None  # first drop after reset: streak=1

    await repository.save(_bank_average_point("3.76", 150))
    alert = await service.check()

    assert alert is not None
    assert alert.current_value == Decimal("3.76")
    assert alert.streak_length == 2
    # last_alerted_value was cleared by the increase, so this counts as a
    # fresh alert even though 3.76 is not below the old 3.72 low.
    assert alert.previous_alerted_value is None
