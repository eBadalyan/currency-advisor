from __future__ import annotations

import logging
from datetime import UTC, datetime
from decimal import Decimal, InvalidOperation

import httpx
from tenacity import AsyncRetrying, retry_if_exception_type, stop_after_attempt, wait_exponential

from app.collectors.base import RateCollector, RatePoint
from app.collectors.exceptions import CollectorResponseError, CollectorTimeoutError
from app.collectors.html_rate_table import HtmlRateRowParser
from app.core.config import get_settings

logger = logging.getLogger(__name__)

# "ACBA Bank" (a commercial bank), not to be confused with the CBACollector's
# "Central Bank of Armenia" — deliberately spelled out in full to avoid that
# confusion in stored data and logs.
SOURCE_NAME = "ACBA Bank"
_BASE_CURRENCY = "RUB"
_QUOTE_CURRENCY = "AMD"

# ACBA's own site labels the Russian ruble "RUR", not "RUB" (verified live)
# — a site-specific quirk that must not leak into our domain model, where
# base_currency stays the canonical "RUB" used across every other source.
_SITE_CURRENCY_CODE = "RUR"

# ACBA's Angular SSR page pre-renders exactly one populated
# <table class="rates-table ...">, for whichever tab is marked active in the
# static HTML — verified live to be "CASH" by default. Other tab-panes exist
# in the markup but are empty placeholders (hydrated client-side on click),
# so there is no risk of HtmlRateRowParser's first-table-wins rule picking up
# a non-cash table instead.
_RATES_TABLE_CLASS = "rates-table"


def _is_rates_table(attrs: dict[str, str | None]) -> bool:
    return _RATES_TABLE_CLASS in (attrs.get("class") or "").split()


# Table columns are [currency, buy, sell, CB]. Column 1 (buy) is what a
# person actually receives handing over physical RUB at a bank counter.
_CASH_BUY_COLUMN = 1


class AcbaBankCollector(RateCollector):
    """Real bank cash exchange rate for RUB/AMD, scraped from ACBA Bank's
    public rates widget (no JSON/XML API is published — verified live).

    Same reasoning as AmeriabankCollector/EvocabankCollector: this tracks
    what a person actually gets exchanging cash, which can move intraday,
    so observed_at is the fetch time rather than a calendar-day fix.
    """

    def __init__(
        self,
        client: httpx.AsyncClient,
        *,
        base_url: str | None = None,
        timeout_seconds: float | None = None,
        max_retries: int | None = None,
    ) -> None:
        settings = get_settings()
        self._client = client
        self._base_url = base_url or settings.acba_bank_base_url
        self._timeout_seconds = (
            timeout_seconds if timeout_seconds is not None else settings.acba_bank_timeout_seconds
        )
        self._max_retries = (
            max_retries if max_retries is not None else settings.acba_bank_max_retries
        )

    async def collect(self) -> list[RatePoint]:
        retrying = AsyncRetrying(
            stop=stop_after_attempt(self._max_retries),
            wait=wait_exponential(multiplier=1, min=1, max=5),
            retry=retry_if_exception_type(CollectorTimeoutError),
            reraise=True,
        )
        async for attempt in retrying:
            with attempt:
                return [await self._request_rate()]
        raise AssertionError("unreachable: AsyncRetrying always raises or returns")

    async def _request_rate(self) -> RatePoint:
        logger.info("acba_bank_collector.request")
        try:
            response = await self._client.get(
                self._base_url, timeout=self._timeout_seconds, follow_redirects=True
            )
        except httpx.TimeoutException as exc:
            logger.warning("acba_bank_collector.timeout")
            raise CollectorTimeoutError("ACBA Bank request timed out") from exc
        except httpx.HTTPError as exc:
            logger.warning("acba_bank_collector.transport_error", extra={"error": str(exc)})
            raise CollectorResponseError(f"ACBA Bank request failed: {exc}") from exc

        if response.status_code >= 400:
            logger.warning(
                "acba_bank_collector.http_error",
                extra={"status_code": response.status_code},
            )
            raise CollectorResponseError(f"ACBA Bank returned HTTP {response.status_code}")

        point = self._parse_rate(response.text)
        logger.info("acba_bank_collector.success", extra={"value": str(point.value)})
        return point

    def _parse_rate(self, html: str) -> RatePoint:
        parser = HtmlRateRowParser(_is_rates_table, _SITE_CURRENCY_CODE)
        parser.feed(html)

        row = parser.row
        if row is None or len(row) <= _CASH_BUY_COLUMN:
            raise CollectorResponseError("ACBA Bank response has no RUR rate row")

        value_text = row[_CASH_BUY_COLUMN]
        try:
            value = Decimal(value_text)
        except InvalidOperation as exc:
            raise CollectorResponseError(
                f"ACBA Bank returned an unparsable RUR rate: {value_text!r}"
            ) from exc

        if value <= 0:
            raise CollectorResponseError(f"ACBA Bank returned a non-positive RUR rate: {value}")

        return RatePoint(
            source=SOURCE_NAME,
            base_currency=_BASE_CURRENCY,
            quote_currency=_QUOTE_CURRENCY,
            value=value,
            observed_at=datetime.now(UTC),
        )
