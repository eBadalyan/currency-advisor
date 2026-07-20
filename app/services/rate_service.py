from dataclasses import dataclass
from decimal import Decimal

from app.providers.base import QuoteProvider


@dataclass(frozen=True, slots=True)
class RateSnapshot:
    official_rub_amd: Decimal
    market_usd_rub: Decimal | None
    official_usd_amd: Decimal
    synthetic_rub_amd: Decimal | None
    deviation_percent: Decimal | None


class RateService:
    def __init__(self, cba: QuoteProvider, market: QuoteProvider | None = None) -> None:
        self._cba = cba
        self._market = market

    async def snapshot(self) -> RateSnapshot:
        rub_amd = await self._cba.get_quote("RUB/AMD")
        usd_amd = await self._cba.get_quote("USD/AMD")

        usd_rub_value: Decimal | None = None
        synthetic: Decimal | None = None
        deviation: Decimal | None = None
        if self._market is not None:
            usd_rub = await self._market.get_quote("USD/RUB")
            usd_rub_value = usd_rub.price
            synthetic = usd_amd.price / usd_rub.price
            deviation = (rub_amd.price / synthetic - Decimal(1)) * Decimal(100)

        return RateSnapshot(
            official_rub_amd=rub_amd.price,
            market_usd_rub=usd_rub_value,
            official_usd_amd=usd_amd.price,
            synthetic_rub_amd=synthetic,
            deviation_percent=deviation,
        )
