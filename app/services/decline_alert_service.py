from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from decimal import Decimal

from app.repositories.exchange_rate import ExchangeRateRepository
from app.repositories.notification_state_repository import (
    NotificationStateRepository,
    NotificationStateSnapshot,
)
from app.services.bank_average_service import SOURCE_NAME as BANK_AVERAGE_SOURCE_NAME

_BASE_CURRENCY = "RUB"
_QUOTE_CURRENCY = "AMD"
_SIGNAL_NAME = "bank_average_decline"


def _utcnow() -> datetime:
    return datetime.now(UTC)


@dataclass(frozen=True, slots=True)
class DeclineAlert:
    current_value: Decimal
    streak_length: int
    previous_alerted_value: Decimal | None


class DeclineAlertService:
    """Trend-following decline detector over the Bank Average median.

    Deliberately decoupled from RuleBasedRecommendationStrategy (mean-
    reversion): a push alert is an early-warning signal, and the RUB cash
    market in Armenia is flow-driven/thin rather than arbitraged, so a
    confirmed decline is treated as likely to persist, not to revert. See
    docs/superpowers/specs/2026-07-24-decline-alert-design.md.
    """

    def __init__(
        self,
        exchange_rates: ExchangeRateRepository,
        notification_states: NotificationStateRepository,
        streak_threshold: int,
    ) -> None:
        self._exchange_rates = exchange_rates
        self._notification_states = notification_states
        self._streak_threshold = streak_threshold

    async def check(self) -> DeclineAlert | None:
        point = await self._exchange_rates.get_latest(
            BANK_AVERAGE_SOURCE_NAME, _BASE_CURRENCY, _QUOTE_CURRENCY
        )
        if point is None:
            return None

        state = await self._notification_states.get(_SIGNAL_NAME)
        if state is None:
            await self._notification_states.save(
                NotificationStateSnapshot(
                    signal_name=_SIGNAL_NAME,
                    last_value=point.value,
                    streak_length=0,
                    last_alerted_value=None,
                    updated_at=_utcnow(),
                )
            )
            return None

        if point.value == state.last_value:
            return None

        if point.value < state.last_value:
            streak_length = state.streak_length + 1
            last_alerted_value = state.last_alerted_value
        else:
            streak_length = 0
            last_alerted_value = None

        alert: DeclineAlert | None = None
        if streak_length >= self._streak_threshold and (
            last_alerted_value is None or point.value < last_alerted_value
        ):
            alert = DeclineAlert(
                current_value=point.value,
                streak_length=streak_length,
                previous_alerted_value=last_alerted_value,
            )
            last_alerted_value = point.value

        await self._notification_states.save(
            NotificationStateSnapshot(
                signal_name=_SIGNAL_NAME,
                last_value=point.value,
                streak_length=streak_length,
                last_alerted_value=last_alerted_value,
                updated_at=_utcnow(),
            )
        )
        return alert
