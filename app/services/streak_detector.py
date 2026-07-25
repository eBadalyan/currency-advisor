from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from decimal import Decimal

Comparator = Callable[[Decimal, Decimal], bool]


@dataclass(frozen=True, slots=True)
class StreakEvaluation:
    streak_length: int
    previous_alerted_value: Decimal | None
    next_last_alerted_value: Decimal | None
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

    `previous_alerted_value` is the carried last_alerted_value BEFORE this
    check (used by callers to populate an Alert's previous_alerted_value
    field). `next_last_alerted_value` is what the caller should persist as
    last_alerted_value going forward — it already reflects the arming
    transition (equals current_value when should_alert is True, otherwise
    equals previous_alerted_value unchanged).
    """
    if continues_trend(current_value, previous_value):
        streak_length = previous_streak_length + 1
        carried = previous_last_alerted_value
    else:
        streak_length = 0
        carried = None

    should_alert = streak_length >= streak_threshold and (
        carried is None or continues_trend(current_value, carried)
    )
    next_last_alerted_value = current_value if should_alert else carried

    return StreakEvaluation(streak_length, carried, next_last_alerted_value, should_alert)
