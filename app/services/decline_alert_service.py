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

    check() does a read (NotificationStateRepository.get) then a write
    (NotificationStateRepository.save) as two separate DB round trips, not
    one transaction. This is safe only because the caller (app/bot/main.py)
    runs this job with max_instances=1 in a single bot process, so calls to
    check() never run concurrently. Running multiple bot replicas, or
    dropping max_instances=1, would introduce a race between the read and
    the write.

    The streak/last-alerted-value branching itself lives in
    app.services.streak_detector.evaluate_streak, shared with
    RiseAlertService (operator.lt here, operator.gt there). See
    docs/superpowers/specs/2026-07-25-rise-alert-design.md for why this was
    extracted rather than duplicated.
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

        evaluation = evaluate_streak(
            current_value=point.value,
            previous_value=state.last_value,
            previous_streak_length=state.streak_length,
            previous_last_alerted_value=state.last_alerted_value,
            streak_threshold=self._streak_threshold,
            continues_trend=operator.lt,
        )

        alert: DeclineAlert | None = None
        last_alerted_value = evaluation.carried_last_alerted_value
        if evaluation.should_alert:
            alert = DeclineAlert(
                current_value=point.value,
                streak_length=evaluation.streak_length,
                previous_alerted_value=evaluation.carried_last_alerted_value,
            )
            last_alerted_value = point.value

        await self._notification_states.save(
            NotificationStateSnapshot(
                signal_name=_SIGNAL_NAME,
                last_value=point.value,
                streak_length=evaluation.streak_length,
                last_alerted_value=last_alerted_value,
                updated_at=_utcnow(),
            )
        )
        return alert
