from __future__ import annotations

from datetime import UTC, datetime, timedelta
from decimal import Decimal

from app.analytics.indicators import (
    local_maximum,
    local_minimum,
    moving_average,
    rate_change_percent,
    volatility,
)
from app.collectors.base import RatePoint

_START = datetime(2026, 7, 1, tzinfo=UTC)


def _points(*values: str) -> list[RatePoint]:
    return [
        RatePoint("Test Source", "RUB", "AMD", Decimal(value), _START + timedelta(days=i))
        for i, value in enumerate(values)
    ]


def test_moving_average_of_exact_window() -> None:
    assert moving_average(_points("4.0", "4.2", "4.4"), window=3) == Decimal("4.2")


def test_moving_average_uses_most_recent_window() -> None:
    assert moving_average(_points("1.0", "4.0", "5.0", "6.0"), window=2) == Decimal("5.5")


def test_moving_average_returns_none_when_not_enough_points() -> None:
    assert moving_average(_points("4.0", "4.2"), window=3) is None


def test_moving_average_returns_none_for_non_positive_window() -> None:
    assert moving_average(_points("4.0"), window=0) is None


def test_rate_change_percent_positive_and_negative() -> None:
    assert rate_change_percent(_points("4.0", "4.4")) == Decimal("10")
    assert rate_change_percent(_points("5.0", "4.0")) == Decimal("-20")


def test_rate_change_percent_returns_none_with_fewer_than_two_points() -> None:
    assert rate_change_percent(_points("4.0")) is None
    assert rate_change_percent([]) is None


def test_rate_change_percent_returns_none_when_first_value_is_zero() -> None:
    assert rate_change_percent(_points("0", "4.0")) is None


def test_volatility_of_constant_series_is_zero() -> None:
    assert volatility(_points("4.0", "4.0", "4.0", "4.0")) == Decimal(0)


def test_volatility_is_positive_for_non_constant_series() -> None:
    result = volatility(_points("4.0", "5.0", "4.0"))
    assert result is not None
    assert result > 0


def test_volatility_returns_none_with_fewer_than_three_points() -> None:
    assert volatility(_points("4.0", "4.2")) is None


def test_local_minimum_and_maximum() -> None:
    points = _points("4.5", "4.2", "4.8", "4.1")
    minimum = local_minimum(points)
    maximum = local_maximum(points)
    assert minimum is not None and minimum.value == Decimal("4.1")
    assert maximum is not None and maximum.value == Decimal("4.8")


def test_local_minimum_and_maximum_return_none_for_empty_sequence() -> None:
    assert local_minimum([]) is None
    assert local_maximum([]) is None
