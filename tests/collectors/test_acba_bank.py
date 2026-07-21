from __future__ import annotations

from collections.abc import Callable
from decimal import Decimal

import httpx
import pytest

from app.collectors.acba_bank import AcbaBankCollector
from app.collectors.exceptions import CollectorResponseError, CollectorTimeoutError
from tests.collectors.contract import assert_returns_valid_rate_points

# Trimmed down from the real ACBA Bank page (verified live): Angular SSR,
# columns [currency, buy, sell, CB]. ACBA's own site labels the ruble "RUR",
# not "RUB". Only one populated "rates-table" exists in the static HTML
# (the active/default tab, verified to be Cash) — other tab-panes in the
# real markup are empty placeholders, which this fixture omits entirely
# since HtmlRateRowParser only ever looks at the first matching table.
_PAGE = """
<table class="rates-table w-full">
<thead><tr><th></th><th>BUY</th><th>SELL</th><th>CB</th></tr></thead>
<tbody>
<tr>
<td><div><app-icon></app-icon><span>USD</span></div></td>
<td><div><span>364.5</span></div></td>
<td><div><span>368.5</span></div></td>
<td><div><span>365.93</span></div></td>
</tr>
<tr>
<td><div><app-icon></app-icon><span>RUR</span></div></td>
<td><div><span>3.73</span></div></td>
<td><div><span>4.78</span></div></td>
<td><div><span>4.6651</span></div></td>
</tr>
</tbody>
</table>
"""

_PAGE_WITHOUT_RUR = _PAGE.replace(
    "<tr>\n"
    "<td><div><app-icon></app-icon><span>RUR</span></div></td>\n"
    "<td><div><span>3.73</span></div></td>\n"
    "<td><div><span>4.78</span></div></td>\n"
    "<td><div><span>4.6651</span></div></td>\n"
    "</tr>\n",
    "",
)


def _html_response(html: str) -> httpx.Response:
    return httpx.Response(200, text=html)


def _success_handler(request: httpx.Request) -> httpx.Response:
    return _html_response(_PAGE)


def _make_collector(
    handler: Callable[[httpx.Request], httpx.Response],
    *,
    max_retries: int = 3,
    timeout_seconds: float = 5.0,
) -> AcbaBankCollector:
    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    return AcbaBankCollector(client, max_retries=max_retries, timeout_seconds=timeout_seconds)


async def test_collect_returns_rub_amd_buy_rate() -> None:
    collector = _make_collector(_success_handler)

    points = await assert_returns_valid_rate_points(collector, expected_min_count=1)

    assert len(points) == 1
    point = points[0]
    assert point.source == "ACBA Bank"
    # base_currency is the canonical "RUB", even though ACBA's own site
    # labels the row "RUR".
    assert point.base_currency == "RUB"
    assert point.quote_currency == "AMD"
    assert point.value == Decimal("3.73")


async def test_retries_on_timeout_then_succeeds() -> None:
    attempts = {"count": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        attempts["count"] += 1
        if attempts["count"] == 1:
            raise httpx.ReadTimeout("mocked timeout", request=request)
        return _html_response(_PAGE)

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


async def test_missing_rur_row_raises_collector_response_error() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return _html_response(_PAGE_WITHOUT_RUR)

    collector = _make_collector(handler)

    with pytest.raises(CollectorResponseError):
        await collector.collect()
