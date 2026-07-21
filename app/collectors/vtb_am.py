from __future__ import annotations

import logging
import re
from datetime import UTC, datetime
from decimal import Decimal, InvalidOperation

import httpx
from tenacity import AsyncRetrying, retry_if_exception_type, stop_after_attempt, wait_exponential

from app.collectors.base import RateCollector, RatePoint
from app.collectors.exceptions import CollectorResponseError, CollectorTimeoutError
from app.core.config import get_settings

logger = logging.getLogger(__name__)

# "VTB Bank (Armenia)", not the Russian parent VTB — this collector only
# ever talks to the Armenian site.
SOURCE_NAME = "VTB Bank (Armenia)"
_BASE_CURRENCY = "RUB"
_QUOTE_CURRENCY = "AMD"

# Unlike the other bank collectors, VTB Armenia's currency page has no HTML
# table at all (verified live): rates are inlined server-side as a plain
# JavaScript object literal in a <script> tag (a `currencyObj` with 'cash'
# and 'nonCash' arrays of single-currency dicts). Scoping the search to the
# 'cash' array first, then to its 'RUB' entry, avoids picking up the
# non-cash rate, which differs.
_CASH_SECTION_RE = re.compile(r"'cash'\s*:\s*\[(.*?)\]\s*,\s*'nonCash'", re.DOTALL)
_CURRENCY_BUY_RE = re.compile(r"'RUB'\s*:\s*\{\s*'buy'\s*:\s*'([0-9.]+)'")


class VtbArmeniaCollector(RateCollector):
    """Real bank cash exchange rate for RUB/AMD, scraped from VTB Armenia's
    currency page (no JSON/XML API is published — verified live).

    Same reasoning as the other bank collectors: this tracks what a person
    actually gets exchanging cash, which can move intraday, so observed_at
    is the fetch time rather than a calendar-day fix.
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
        self._base_url = base_url or settings.vtb_am_base_url
        self._timeout_seconds = (
            timeout_seconds if timeout_seconds is not None else settings.vtb_am_timeout_seconds
        )
        self._max_retries = max_retries if max_retries is not None else settings.vtb_am_max_retries

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
        logger.info("vtb_am_collector.request")
        try:
            response = await self._client.get(
                self._base_url, timeout=self._timeout_seconds, follow_redirects=True
            )
        except httpx.TimeoutException as exc:
            logger.warning("vtb_am_collector.timeout")
            raise CollectorTimeoutError("VTB Armenia request timed out") from exc
        except httpx.HTTPError as exc:
            logger.warning("vtb_am_collector.transport_error", extra={"error": str(exc)})
            raise CollectorResponseError(f"VTB Armenia request failed: {exc}") from exc

        if response.status_code >= 400:
            logger.warning(
                "vtb_am_collector.http_error",
                extra={"status_code": response.status_code},
            )
            raise CollectorResponseError(f"VTB Armenia returned HTTP {response.status_code}")

        point = self._parse_rate(response.text)
        logger.info("vtb_am_collector.success", extra={"value": str(point.value)})
        return point

    def _parse_rate(self, html: str) -> RatePoint:
        cash_section = _CASH_SECTION_RE.search(html)
        if cash_section is None:
            raise CollectorResponseError("VTB Armenia response has no 'cash' rates section")

        buy_match = _CURRENCY_BUY_RE.search(cash_section.group(1))
        if buy_match is None:
            raise CollectorResponseError("VTB Armenia response has no cash RUB rate")

        value_text = buy_match.group(1)
        try:
            value = Decimal(value_text)
        except InvalidOperation as exc:
            raise CollectorResponseError(
                f"VTB Armenia returned an unparsable RUB rate: {value_text!r}"
            ) from exc

        if value <= 0:
            raise CollectorResponseError(f"VTB Armenia returned a non-positive RUB rate: {value}")

        return RatePoint(
            source=SOURCE_NAME,
            base_currency=_BASE_CURRENCY,
            quote_currency=_QUOTE_CURRENCY,
            value=value,
            observed_at=datetime.now(UTC),
        )
