from __future__ import annotations

from decimal import Decimal

from app.analytics.service import AnalyticsService
from app.collectors.cba import SOURCE_NAME as CBA_SOURCE_NAME
from app.collectors.cbr import SOURCE_NAME as CBR_SOURCE_NAME
from app.recommendations.models import Recommendation, RecommendationContext
from app.recommendations.strategy import RecommendationStrategy
from app.repositories.exchange_rate import ExchangeRateRepository
from app.services.bank_average_service import SOURCE_NAME as BANK_AVERAGE_SOURCE_NAME

_BASE_CURRENCY = "RUB"
_QUOTE_CURRENCY = "AMD"
_BRIDGE_CURRENCY = "USD"


class RecommendationService:
    """Assembles a RecommendationContext for RUB/AMD and hands it to a
    RecommendationStrategy. Specialized to this one pair — the product's
    stated focus — rather than a generic multi-pair engine that isn't
    needed yet.
    """

    def __init__(
        self,
        repository: ExchangeRateRepository,
        analytics: AnalyticsService,
        strategy: RecommendationStrategy,
    ) -> None:
        self._repository = repository
        self._analytics = analytics
        self._strategy = strategy

    async def get_recommendation(self, *, window_days: int = 30) -> Recommendation | None:
        official_rate = await self._repository.get_latest(
            CBA_SOURCE_NAME, _BASE_CURRENCY, _QUOTE_CURRENCY
        )
        if official_rate is None:
            return None

        indicators = await self._analytics.get_indicators(
            CBA_SOURCE_NAME, _BASE_CURRENCY, _QUOTE_CURRENCY, window_days=window_days
        )
        synthetic_rate = await self._get_synthetic_rate()
        deviation = self._deviation_percent(official_rate.value, synthetic_rate)

        bank_point = await self._repository.get_latest(
            BANK_AVERAGE_SOURCE_NAME, _BASE_CURRENCY, _QUOTE_CURRENCY
        )
        bank_median_rate = bank_point.value if bank_point is not None else None
        bank_deviation = self._deviation_percent(official_rate.value, bank_median_rate)

        context = RecommendationContext(
            official_rate=official_rate,
            indicators=indicators,
            synthetic_rate=synthetic_rate,
            source_deviation_percent=deviation,
            bank_median_rate=bank_median_rate,
            bank_rate_deviation_percent=bank_deviation,
        )
        return self._strategy.evaluate(context)

    async def _get_synthetic_rate(self) -> Decimal | None:
        # RUB/AMD via USD as a bridge: CBA's own USD/AMD divided by CBR's
        # USD/RUB. Compared against the official rate as a cross-source
        # sanity check (see RuleBasedRecommendationStrategy) — not a second
        # source of truth for RUB/AMD itself.
        usd_amd = await self._repository.get_latest(
            CBA_SOURCE_NAME, _BRIDGE_CURRENCY, _QUOTE_CURRENCY
        )
        usd_rub = await self._repository.get_latest(
            CBR_SOURCE_NAME, _BRIDGE_CURRENCY, _BASE_CURRENCY
        )
        if usd_amd is None or usd_rub is None or usd_rub.value == 0:
            return None
        return usd_amd.value / usd_rub.value

    @staticmethod
    def _deviation_percent(official: Decimal, synthetic: Decimal | None) -> Decimal | None:
        if synthetic is None or synthetic == 0:
            return None
        return (official / synthetic - 1) * Decimal(100)
