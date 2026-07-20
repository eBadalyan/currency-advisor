from __future__ import annotations

import logging

from app.collectors.base import RateCollector, RatePoint
from app.repositories.exchange_rate import ExchangeRateRepository

logger = logging.getLogger(__name__)


class RateService:
    """Connects the Repository to Collectors (write path) and to callers
    that need a business-facing read (currently just the Bot — the API
    reads straight through the Repository instead, since its routes add
    no logic on top of a plain lookup).

    Takes the collector as a parameter rather than binding one at
    construction time, so a single service instance can run any
    RateCollector implementation (CBA today, further sources later)
    without changes here.
    """

    def __init__(self, repository: ExchangeRateRepository) -> None:
        self._repository = repository

    async def collect_and_save(self, collector: RateCollector) -> list[RatePoint]:
        points = await collector.collect()
        await self._repository.bulk_save(points)
        logger.info("rate_service.collect_and_save", extra={"count": len(points)})
        return points

    async def get_latest_rate(
        self, source: str, base_currency: str, quote_currency: str
    ) -> RatePoint | None:
        return await self._repository.get_latest(source, base_currency, quote_currency)
