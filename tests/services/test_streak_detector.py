from __future__ import annotations

import operator
from decimal import Decimal

from app.services.streak_detector import evaluate_streak


def test_continuing_move_increments_streak_and_carries_last_alerted() -> None:
    result = evaluate_streak(
        current_value=Decimal("3.70"),
        previous_value=Decimal("3.72"),
        previous_streak_length=1,
        previous_last_alerted_value=None,
        streak_threshold=2,
        continues_trend=operator.lt,
    )

    assert result.streak_length == 2
    assert result.previous_alerted_value is None
    assert result.next_last_alerted_value == Decimal("3.70")
    assert result.should_alert is True


def test_broken_move_resets_streak_and_clears_last_alerted() -> None:
    result = evaluate_streak(
        current_value=Decimal("3.80"),
        previous_value=Decimal("3.72"),
        previous_streak_length=2,
        previous_last_alerted_value=Decimal("3.72"),
        streak_threshold=2,
        continues_trend=operator.lt,
    )

    assert result.streak_length == 0
    assert result.previous_alerted_value is None
    assert result.next_last_alerted_value is None
    assert result.should_alert is False


def test_no_alert_below_threshold() -> None:
    result = evaluate_streak(
        current_value=Decimal("3.74"),
        previous_value=Decimal("3.76"),
        previous_streak_length=0,
        previous_last_alerted_value=None,
        streak_threshold=2,
        continues_trend=operator.lt,
    )

    assert result.streak_length == 1
    assert result.next_last_alerted_value is None
    assert result.should_alert is False


def test_realert_on_new_extreme_beyond_last_alerted() -> None:
    result = evaluate_streak(
        current_value=Decimal("3.71"),
        previous_value=Decimal("3.72"),
        previous_streak_length=2,
        previous_last_alerted_value=Decimal("3.72"),
        streak_threshold=2,
        continues_trend=operator.lt,
    )

    assert result.streak_length == 3
    assert result.previous_alerted_value == Decimal("3.72")
    assert result.next_last_alerted_value == Decimal("3.71")
    assert result.should_alert is True


def test_no_realert_when_streak_continues_without_a_new_extreme() -> None:
    # Streak continues (3.72 < previous_value 3.73) but does not go past the
    # already-alerted floor of 3.72 itself — must not re-alert.
    result = evaluate_streak(
        current_value=Decimal("3.72"),
        previous_value=Decimal("3.73"),
        previous_streak_length=2,
        previous_last_alerted_value=Decimal("3.72"),
        streak_threshold=2,
        continues_trend=operator.lt,
    )

    assert result.streak_length == 3
    assert result.next_last_alerted_value == Decimal("3.72")
    assert result.should_alert is False


def test_rise_direction_mirrors_decline_with_operator_gt() -> None:
    result = evaluate_streak(
        current_value=Decimal("3.80"),
        previous_value=Decimal("3.78"),
        previous_streak_length=1,
        previous_last_alerted_value=None,
        streak_threshold=2,
        continues_trend=operator.gt,
    )

    assert result.streak_length == 2
    assert result.previous_alerted_value is None
    assert result.next_last_alerted_value == Decimal("3.80")
    assert result.should_alert is True


def test_rise_direction_resets_on_a_drop() -> None:
    result = evaluate_streak(
        current_value=Decimal("3.74"),
        previous_value=Decimal("3.80"),
        previous_streak_length=2,
        previous_last_alerted_value=Decimal("3.80"),
        streak_threshold=2,
        continues_trend=operator.gt,
    )

    assert result.streak_length == 0
    assert result.previous_alerted_value is None
    assert result.next_last_alerted_value is None
    assert result.should_alert is False
