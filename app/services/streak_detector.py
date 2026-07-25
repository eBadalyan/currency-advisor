from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from decimal import Decimal

Comparator = Callable[[Decimal, Decimal], bool]


@dataclass(frozen=True, slots=True)
class StreakEvaluation:
    streak_length: int
    carried_last_alerted_value: Decimal | None
    should_alert: bool


def evaluate_streak(
    current_value: Decimal,
    previous_value: Decimal,
    previous_streak_length: int,
    previous_last_alerted_value: Decimal | None,
    streak_threshold: int,
    continues_trend: Comparator,
) -> StreakEvaluation:
    """Shared branching logic behind DeclineAlertService/RiseAlertService.

    continues_trend(current, reference) must be True when `current` extends
    the tracked trend relative to `reference` (operator.lt for a decline
    streak, operator.gt for a rise streak). Callers are responsible for the
    bootstrap (no prior state) and current-equals-previous-value cases —
    this function only computes the streak/re-alert branching once a caller
    has already confirmed the value actually changed.
    """
    if continues_trend(current_value, previous_value):
        streak_length = previous_streak_length + 1
        carried_last_alerted_value = previous_last_alerted_value
    else:
        streak_length = 0
        carried_last_alerted_value = None

    should_alert = streak_length >= streak_threshold and (
        carried_last_alerted_value is None
        or continues_trend(current_value, carried_last_alerted_value)
    )

    return StreakEvaluation(streak_length, carried_last_alerted_value, should_alert)
