from datetime import UTC, datetime
from decimal import Decimal

import pytest

from app.providers.base import Quote, QuoteProvider
from app.services.rate_service import RateService


class FakeCBA(QuoteProvider):
    async def get_quote(self, symbol: str) -> Quote:
        values = {"RUB/AMD": Decimal("4.60"), "USD/AMD": Decimal("368")}
        return Quote(symbol, values[symbol], datetime.now(UTC), "fake", True)


class FakeMarket(QuoteProvider):
    async def get_quote(self, symbol: str) -> Quote:
        return Quote(symbol, Decimal("80"), datetime.now(UTC), "fake")


@pytest.mark.asyncio
async def test_synthetic_cross_rate() -> None:
    snapshot = await RateService(FakeCBA(), FakeMarket()).snapshot()
    assert snapshot.synthetic_rub_amd == Decimal("4.6")
    assert snapshot.deviation_percent == Decimal("0")
