from __future__ import annotations

from collections.abc import Sequence
from decimal import Decimal

from app.collectors.base import RatePoint

# All functions here expect `points` in chronological order (oldest first).
# ExchangeRateRepository.list_history() returns newest-first, so callers
# (AnalyticsService) reverse it before calling into this module.


def moving_average(points: Sequence[RatePoint], window: int) -> Decimal | None:
    if window <= 0 or len(points) < window:
        return None
    recent = points[-window:]
    total = sum((point.value for point in recent), start=Decimal(0))
    return total / window


def rate_change_percent(points: Sequence[RatePoint]) -> Decimal | None:
    if len(points) < 2:
        return None
    first, last = points[0].value, points[-1].value
    if first == 0:
        return None
    return (last - first) / first * Decimal(100)


def volatility(points: Sequence[RatePoint]) -> Decimal | None:
    """Sample standard deviation of period-over-period returns."""
    if len(points) < 3:
        return None

    returns: list[Decimal] = []
    for previous, current in zip(points, points[1:], strict=False):
        if previous.value == 0:
            return None
        returns.append((current.value - previous.value) / previous.value)

    mean = sum(returns, start=Decimal(0)) / len(returns)
    variance = sum(((r - mean) ** 2 for r in returns), start=Decimal(0)) / (len(returns) - 1)
    return variance.sqrt()


def local_minimum(points: Sequence[RatePoint]) -> RatePoint | None:
    return min(points, key=lambda point: point.value, default=None)


def local_maximum(points: Sequence[RatePoint]) -> RatePoint | None:
    return max(points, key=lambda point: point.value, default=None)
