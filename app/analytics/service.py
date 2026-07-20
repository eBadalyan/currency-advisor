from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal

from app.analytics.indicators import (
    local_maximum,
    local_minimum,
    moving_average,
    rate_change_percent,
    volatility,
)
from app.collectors.base import RatePoint
from app.repositories.exchange_rate import ExchangeRateRepository


@dataclass(frozen=True, slots=True)
class RateIndicators:
    sample_size: int
    moving_average: Decimal | None
    rate_change_percent: Decimal | None
    volatility: Decimal | None
    minimum: RatePoint | None
    maximum: RatePoint | None


class AnalyticsService:
    """Computes indicators over a window of already-persisted rate history.

    Depends only on the Repository — no Collector, no API/Bot — so it can be
    used as soon as enough data has accumulated, independent of those stages.
    """

    def __init__(self, repository: ExchangeRateRepository) -> None:
        self._repository = repository

    async def get_indicators(
        self, source: str, base_currency: str, quote_currency: str, *, window: int = 30
    ) -> RateIndicators:
        history = await self._repository.list_history(
            source, base_currency, quote_currency, limit=window
        )
        # list_history() is newest-first; indicators expect chronological order.
        points = list(reversed(history))

        return RateIndicators(
            sample_size=len(points),
            moving_average=moving_average(points, window=len(points)) if points else None,
            rate_change_percent=rate_change_percent(points),
            volatility=volatility(points),
            minimum=local_minimum(points),
            maximum=local_maximum(points),
        )
