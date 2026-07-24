from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal

from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.notification_state import NotificationState


@dataclass(frozen=True, slots=True)
class NotificationStateSnapshot:
    signal_name: str
    last_value: Decimal
    streak_length: int
    last_alerted_value: Decimal | None
    updated_at: datetime


class NotificationStateRepository:
    """Sole owner of notification_states persistence.

    Works in NotificationStateSnapshot at its boundary in both directions —
    callers never see the SQLAlchemy model, same pattern as
    ExchangeRateRepository/RatePoint.
    """

    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def get(self, signal_name: str) -> NotificationStateSnapshot | None:
        stmt = select(NotificationState).where(NotificationState.signal_name == signal_name)
        row = (await self._session.execute(stmt)).scalar_one_or_none()
        return self._to_snapshot(row) if row is not None else None

    async def save(self, state: NotificationStateSnapshot) -> None:
        values = {
            "signal_name": state.signal_name,
            "last_value": state.last_value,
            "streak_length": state.streak_length,
            "last_alerted_value": state.last_alerted_value,
            "updated_at": state.updated_at,
        }
        stmt = insert(NotificationState).values(**values)
        stmt = stmt.on_conflict_do_update(index_elements=["signal_name"], set_=values)
        await self._session.execute(stmt)
        await self._session.commit()

    @staticmethod
    def _to_snapshot(row: NotificationState) -> NotificationStateSnapshot:
        return NotificationStateSnapshot(
            signal_name=row.signal_name,
            last_value=row.last_value,
            streak_length=row.streak_length,
            last_alerted_value=row.last_alerted_value,
            updated_at=row.updated_at,
        )
