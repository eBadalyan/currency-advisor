from __future__ import annotations

from collections.abc import Callable
from datetime import UTC, datetime
from decimal import Decimal

import httpx
import pytest

from app.collectors.ameriabank import AmeriabankCollector
from app.collectors.exceptions import CollectorResponseError, CollectorTimeoutError
from tests.collectors.contract import assert_returns_valid_rate_points

# Trimmed down from the real Ameriabank page (verified live): a plain
# server-rendered ASP.NET/DNN grid, id has a per-deployment numeric prefix.
# Columns are [currency, cash buy, cash sell, non-cash buy, non-cash sell].
_RATES_TABLE = """
<table id="dnn_ctr16862_View_grdRates">
<tr class="Header">
<td class="HeaderCell"></td>
<td class="HeaderCell" colspan="2">կանխիկ</td>
<td class="HeaderCell" colspan="2">անկանխիկ</td>
</tr>
<tr class="Header">
<th scope="col">&nbsp;</th>
<th scope="col">առք</th><th scope="col">վաճառք</th>
<th scope="col">առք</th><th scope="col">վաճառք</th>
</tr>
<tr class="Item">
<td align="center">USD</td>
<td align="right">363.50</td><td align="right">368.50</td>
<td align="right">363.50</td><td align="right">368.50</td>
</tr>
<tr class="Item">
<td align="center">RUB</td>
<td align="right">3.72</td><td align="right">4.67</td>
<td align="right">4.46</td><td align="right">4.86</td>
</tr>
<tr class="Item">
<td align="center">CNY</td>
<td align="right">52.00</td><td align="right">56.00</td>
<td align="right">52.00</td><td align="right">56.00</td>
</tr>
</table>
"""

_PAGE_WITHOUT_RUB = _RATES_TABLE.replace(
    '<tr class="Item">\n'
    '<td align="center">RUB</td>\n'
    '<td align="right">3.72</td><td align="right">4.67</td>\n'
    '<td align="right">4.46</td><td align="right">4.86</td>\n'
    "</tr>\n",
    "",
)

_PAGE_WITH_MISSING_CASH_RATE = _RATES_TABLE.replace(
    '<td align="center">RUB</td>\n<td align="right">3.72</td>',
    '<td align="center">RUB</td>\n<td align="right">&nbsp;</td>',
)


def _html_response(html: str) -> httpx.Response:
    return httpx.Response(200, text=html)


def _success_handler(request: httpx.Request) -> httpx.Response:
    return _html_response(_RATES_TABLE)


def _make_collector(
    handler: Callable[[httpx.Request], httpx.Response],
    *,
    max_retries: int = 3,
    timeout_seconds: float = 5.0,
) -> AmeriabankCollector:
    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    return AmeriabankCollector(client, max_retries=max_retries, timeout_seconds=timeout_seconds)


async def test_collect_returns_rub_amd_cash_buy_rate() -> None:
    collector = _make_collector(_success_handler)

    points = await assert_returns_valid_rate_points(collector, expected_min_count=1)

    assert len(points) == 1
    point = points[0]
    assert point.source == "Ameriabank"
    assert point.base_currency == "RUB"
    assert point.quote_currency == "AMD"
    assert point.value == Decimal("3.72")


async def test_observed_at_is_a_recent_utc_timestamp() -> None:
    collector = _make_collector(_success_handler)

    before = datetime.now(UTC)
    points = await collector.collect()
    after = datetime.now(UTC)

    assert before <= points[0].observed_at <= after


async def test_retries_on_timeout_then_succeeds() -> None:
    attempts = {"count": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        attempts["count"] += 1
        if attempts["count"] == 1:
            raise httpx.ReadTimeout("mocked timeout", request=request)
        return _html_response(_RATES_TABLE)

    collector = _make_collector(handler)

    points = await collector.collect()

    assert attempts["count"] == 2
    assert len(points) == 1


async def test_exhausts_retries_raises_collector_timeout_error() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ReadTimeout("mocked timeout", request=request)

    collector = _make_collector(handler, max_retries=2)

    with pytest.raises(CollectorTimeoutError):
        await collector.collect()


async def test_http_error_status_raises_collector_response_error() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(500, text="internal error")

    collector = _make_collector(handler)

    with pytest.raises(CollectorResponseError):
        await collector.collect()


async def test_missing_rub_row_raises_collector_response_error() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return _html_response(_PAGE_WITHOUT_RUB)

    collector = _make_collector(handler)

    with pytest.raises(CollectorResponseError):
        await collector.collect()


async def test_missing_cash_rate_value_raises_collector_response_error() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return _html_response(_PAGE_WITH_MISSING_CASH_RATE)

    collector = _make_collector(handler)

    with pytest.raises(CollectorResponseError):
        await collector.collect()


async def test_missing_rates_table_raises_collector_response_error() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return _html_response("<html><body>no table here</body></html>")

    collector = _make_collector(handler)

    with pytest.raises(CollectorResponseError):
        await collector.collect()
