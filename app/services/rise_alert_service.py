from __future__ import annotations

import operator
from dataclasses import dataclass
from datetime import UTC, datetime
from decimal import Decimal

from app.repositories.exchange_rate import ExchangeRateRepository
from app.repositories.notification_state_repository import (
    NotificationStateRepository,
    NotificationStateSnapshot,
)
from app.services.bank_average_service import SOURCE_NAME as BANK_AVERAGE_SOURCE_NAME
from app.services.streak_detector import evaluate_streak

_BASE_CURRENCY = "RUB"
_QUOTE_CURRENCY = "AMD"
_SIGNAL_NAME = "bank_average_rise"


def _utcnow() -> datetime:
    return datetime.now(UTC)


@dataclass(frozen=True, slots=True)
class RiseAlert:
    current_value: Decimal
    streak_length: int
    previous_alerted_value: Decimal | None


class RiseAlertService:
    """Informational rise detector over the Bank Average median.

    Purely descriptive ("RUB is strengthening N times in a row") — does
    NOT predict a peak or reversal; that is deferred to a future ML-based
    phase (see docs/superpowers/specs/2026-07-25-rise-alert-design.md).
    Like DeclineAlertService, this is deliberately decoupled from
    RuleBasedRecommendationStrategy (used by /advice) — the two can
    disagree without that being a bug; /advice answers a different
    question (mean-reversion relative to recent history) than this alert
    (a confirmed, ongoing rise).
    Structurally identical to DeclineAlertService with the streak direction
    flipped (operator.gt instead of operator.lt); the shared branching logic
    lives in app.services.streak_detector.evaluate_streak.

    Same non-atomic get-then-save caveat as DeclineAlertService: check()
    does a read then a write as two separate DB round trips, safe only
    because the caller (app/bot/main.py) runs this job with max_instances=1
    in a single bot process.
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

    async def check(self) -> RiseAlert | None:
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

        evaluation = evaluate_streak(
            current_value=point.value,
            previous_value=state.last_value,
            previous_streak_length=state.streak_length,
            previous_last_alerted_value=state.last_alerted_value,
            streak_threshold=self._streak_threshold,
            continues_trend=operator.gt,
        )

        alert: RiseAlert | None = None
        if evaluation.should_alert:
            alert = RiseAlert(
                current_value=point.value,
                streak_length=evaluation.streak_length,
                previous_alerted_value=evaluation.previous_alerted_value,
            )

        await self._notification_states.save(
            NotificationStateSnapshot(
                signal_name=_SIGNAL_NAME,
                last_value=point.value,
                streak_length=evaluation.streak_length,
                last_alerted_value=evaluation.next_last_alerted_value,
                updated_at=_utcnow(),
            )
        )
        return alert
