from __future__ import annotations

from decimal import Decimal

from app.collectors.base import RateCollector, RatePoint


async def assert_returns_valid_rate_points(
    collector: RateCollector, *, expected_min_count: int = 1
) -> list[RatePoint]:
    """Reusable contract check every RateCollector implementation must satisfy."""
    points = await collector.collect()
    assert len(points) >= expected_min_count

    for point in points:
        assert isinstance(point, RatePoint)
        assert isinstance(point.value, Decimal)
        assert point.value > 0
        assert point.observed_at.tzinfo is not None
        assert point.base_currency
        assert point.quote_currency
        assert point.source

    return points
