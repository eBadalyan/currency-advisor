from __future__ import annotations

import logging
from datetime import UTC, datetime
from decimal import Decimal, InvalidOperation
from html.parser import HTMLParser

import httpx
from tenacity import AsyncRetrying, retry_if_exception_type, stop_after_attempt, wait_exponential

from app.collectors.base import RateCollector, RatePoint
from app.collectors.exceptions import CollectorResponseError, CollectorTimeoutError
from app.core.config import get_settings

logger = logging.getLogger(__name__)

SOURCE_NAME = "Ameriabank"
_BASE_CURRENCY = "RUB"
_QUOTE_CURRENCY = "AMD"

# The rates widget is rendered server-side by an ASP.NET/DNN module whose
# element id has a per-deployment numeric prefix (e.g. "dnn_ctr16862_View_grdRates")
# — matching on the stable suffix instead of the full id survives that prefix
# changing across page reloads/deploys.
_RATES_TABLE_ID_SUFFIX = "grdRates"

# Table columns are [currency, cash buy, cash sell, non-cash(card) buy, non-cash sell].
# Column 1 (cash buy) is what a person actually receives handing over physical
# RUB at a bank counter — the real-world number this product cares about, as
# opposed to CBA/CBR's official reference rates.
_CASH_BUY_COLUMN = 1


class _RatesTableParser(HTMLParser):
    """Extracts the RUB row from Ameriabank's exchange-rate widget table.

    No JSON/XML API is published for this data (verified live) — the table is
    plain server-rendered HTML, so a minimal stdlib parser is enough; no
    dependency on a full HTML library is warranted for one table lookup.
    """

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self._table_depth = 0
        self._in_target_table = False
        self._current_row: list[str] | None = None
        self._current_cell: list[str] | None = None
        self.rub_row: list[str] | None = None

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag == "table":
            if self._in_target_table:
                self._table_depth += 1
            elif (dict(attrs).get("id") or "").endswith(_RATES_TABLE_ID_SUFFIX):
                self._in_target_table = True
                self._table_depth = 1
        elif self._in_target_table and tag == "tr":
            self._current_row = []
        elif self._in_target_table and tag == "td":
            self._current_cell = []

    def handle_endtag(self, tag: str) -> None:
        if tag == "table" and self._in_target_table:
            self._table_depth -= 1
            if self._table_depth == 0:
                self._in_target_table = False
        elif tag == "td" and self._current_cell is not None:
            if self._current_row is not None:
                self._current_row.append("".join(self._current_cell).strip())
            self._current_cell = None
        elif tag == "tr" and self._current_row is not None:
            if self.rub_row is None and self._current_row[:1] == [_BASE_CURRENCY]:
                self.rub_row = self._current_row
            self._current_row = None

    def handle_data(self, data: str) -> None:
        if self._current_cell is not None:
            self._current_cell.append(data)


class AmeriabankCollector(RateCollector):
    """Real bank cash exchange rate for RUB/AMD, scraped from Ameriabank's
    public rates widget.

    Unlike CBA/CBR's once-daily official reference rates, this reflects what
    a person actually gets at a bank counter and can move intraday, so
    observed_at is the fetch time rather than a calendar-day fix.
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
        self._base_url = base_url or settings.ameriabank_base_url
        self._timeout_seconds = (
            timeout_seconds if timeout_seconds is not None else settings.ameriabank_timeout_seconds
        )
        self._max_retries = (
            max_retries if max_retries is not None else settings.ameriabank_max_retries
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
        logger.info("ameriabank_collector.request")
        try:
            # The homepage issues a DNN splash-page redirect (verified live)
            # before reaching the page that actually renders the rates
            # widget — explicit per-request follow_redirects, since the
            # shared httpx.AsyncClient the app constructs doesn't enable it.
            response = await self._client.get(
                self._base_url, timeout=self._timeout_seconds, follow_redirects=True
            )
        except httpx.TimeoutException as exc:
            logger.warning("ameriabank_collector.timeout")
            raise CollectorTimeoutError("Ameriabank request timed out") from exc
        except httpx.HTTPError as exc:
            logger.warning("ameriabank_collector.transport_error", extra={"error": str(exc)})
            raise CollectorResponseError(f"Ameriabank request failed: {exc}") from exc

        if response.status_code >= 400:
            logger.warning(
                "ameriabank_collector.http_error",
                extra={"status_code": response.status_code},
            )
            raise CollectorResponseError(f"Ameriabank returned HTTP {response.status_code}")

        point = self._parse_rate(response.text)
        logger.info("ameriabank_collector.success", extra={"value": str(point.value)})
        return point

    def _parse_rate(self, html: str) -> RatePoint:
        parser = _RatesTableParser()
        parser.feed(html)

        row = parser.rub_row
        if row is None or len(row) <= _CASH_BUY_COLUMN:
            raise CollectorResponseError("Ameriabank response has no RUB rate row")

        value_text = row[_CASH_BUY_COLUMN]
        try:
            value = Decimal(value_text)
        except InvalidOperation as exc:
            raise CollectorResponseError(
                f"Ameriabank returned an unparsable RUB rate: {value_text!r}"
            ) from exc

        if value <= 0:
            raise CollectorResponseError(f"Ameriabank returned a non-positive RUB rate: {value}")

        return RatePoint(
            source=SOURCE_NAME,
            base_currency=_BASE_CURRENCY,
            quote_currency=_QUOTE_CURRENCY,
            value=value,
            observed_at=datetime.now(UTC),
        )
