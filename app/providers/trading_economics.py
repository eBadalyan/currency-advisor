from datetime import UTC, datetime
from decimal import Decimal

import httpx
from tenacity import retry, stop_after_attempt, wait_exponential

from app.providers.base import Quote, QuoteProvider


class TradingEconomicsProvider(QuoteProvider):
    """Near-real-time market quotes from Trading Economics."""

    def __init__(self, api_key: str, timeout: float = 15.0) -> None:
        self._api_key = api_key
        self._timeout = timeout

    @retry(stop=stop_after_attempt(3), wait=wait_exponential(min=1, max=5), reraise=True)
    async def get_quote(self, symbol: str) -> Quote:
        market_symbol = symbol.replace("/", "").upper()
        url = f"https://api.tradingeconomics.com/markets/symbol/{market_symbol}:cur"
        params = {"c": self._api_key}
        async with httpx.AsyncClient(timeout=self._timeout) as client:
            response = await client.get(url, params=params)
            response.raise_for_status()
            payload = response.json()

        if not payload:
            raise RuntimeError(f"No Trading Economics quote for {symbol}")
        row = payload[0]
        price_value = row.get("Last") or row.get("Close")
        if price_value is None:
            raise RuntimeError(f"Trading Economics response has no price for {symbol}")

        return Quote(
            symbol=symbol.upper(),
            price=Decimal(str(price_value)),
            observed_at=datetime.now(UTC),
            source="Trading Economics",
            is_official=False,
        )
