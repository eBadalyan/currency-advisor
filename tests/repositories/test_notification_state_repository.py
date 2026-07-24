from __future__ import annotations

from datetime import UTC, datetime
from decimal import Decimal

from sqlalchemy.ext.asyncio import AsyncSession

from app.repositories.notification_state_repository import (
    NotificationStateRepository,
    NotificationStateSnapshot,
)

_UPDATED_AT = datetime(2026, 7, 24, 8, 0, tzinfo=UTC)


def _snapshot(
    *,
    signal_name: str = "bank_average_decline",
    last_value: Decimal = Decimal("3.76"),
    streak_length: int = 0,
    last_alerted_value: Decimal | None = None,
    updated_at: datetime = _UPDATED_AT,
) -> NotificationStateSnapshot:
    return NotificationStateSnapshot(
        signal_name=signal_name,
        last_value=last_value,
        streak_length=streak_length,
        last_alerted_value=last_alerted_value,
        updated_at=updated_at,
    )


async def test_get_returns_none_when_no_state(db_session: AsyncSession) -> None:
    repository = NotificationStateRepository(db_session)

    assert await repository.get("bank_average_decline") is None


async def test_save_then_get_round_trips(db_session: AsyncSession) -> None:
    repository = NotificationStateRepository(db_session)
    state = _snapshot(streak_length=2, last_alerted_value=Decimal("3.72"))

    await repository.save(state)
    loaded = await repository.get("bank_average_decline")

    assert loaded == state


async def test_save_upserts_existing_signal(db_session: AsyncSession) -> None:
    repository = NotificationStateRepository(db_session)
    await repository.save(_snapshot(streak_length=1))

    await repository.save(_snapshot(streak_length=2, last_alerted_value=Decimal("3.70")))
    loaded = await repository.get("bank_average_decline")

    assert loaded is not None
    assert loaded.streak_length == 2
    assert loaded.last_alerted_value == Decimal("3.70")


async def test_save_can_clear_last_alerted_value(db_session: AsyncSession) -> None:
    repository = NotificationStateRepository(db_session)
    await repository.save(_snapshot(last_alerted_value=Decimal("3.70")))

    await repository.save(_snapshot(last_alerted_value=None))
    loaded = await repository.get("bank_average_decline")

    assert loaded is not None
    assert loaded.last_alerted_value is None


async def test_different_signal_names_are_independent(db_session: AsyncSession) -> None:
    repository = NotificationStateRepository(db_session)
    await repository.save(_snapshot(signal_name="signal_a", last_value=Decimal("1.0")))
    await repository.save(_snapshot(signal_name="signal_b", last_value=Decimal("2.0")))

    a = await repository.get("signal_a")
    b = await repository.get("signal_b")

    assert a is not None and a.last_value == Decimal("1.0")
    assert b is not None and b.last_value == Decimal("2.0")
