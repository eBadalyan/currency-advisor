from __future__ import annotations

from collections.abc import Callable
from decimal import Decimal

import httpx
import pytest

from app.collectors.evocabank import EvocabankCollector
from app.collectors.exceptions import CollectorResponseError, CollectorTimeoutError
from tests.collectors.contract import assert_returns_valid_rate_points

# Trimmed down from the real Evocabank page (verified live): two
# "exchange__table" tables (Cash then Non-Cash), Cash's wrapper has no "dn"
# (display:none) class and appears first — the pattern HtmlRateRowParser
# relies on to stop at the right one.
_PAGE = """
<div class="tab-wrapper">
<table class="exchange__table">
<tr>
<td class="exchange__table-cell">
<span class="exchange__icon exchange__icon--usd">USD</span>
</td>
<td class="exchange__table-cell down-icon">363</td>
<td class="exchange__table-cell up-icon">368</td>
</tr>
<tr>
<td class="exchange__table-cell">
<span class="exchange__icon exchange__icon--rub">RUB</span>
</td>
<td class="exchange__table-cell down-icon">3.8</td>
<td class="exchange__table-cell up-icon">4.6</td>
</tr>
</table>
</div>
<div class="tab-wrapper dn">
<table class="exchange__table">
<tr>
<td class="exchange__table-cell">
<span class="exchange__icon exchange__icon--rub">RUB</span>
</td>
<td class="exchange__table-cell down-icon">4.0</td>
<td class="exchange__table-cell up-icon">4.9</td>
</tr>
</table>
</div>
"""

_PAGE_WITHOUT_RUB = _PAGE.replace(
    '<tr>\n<td class="exchange__table-cell">\n'
    '<span class="exchange__icon exchange__icon--rub">RUB</span>\n'
    "</td>\n"
    '<td class="exchange__table-cell down-icon">3.8</td>\n'
    '<td class="exchange__table-cell up-icon">4.6</td>\n'
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
) -> EvocabankCollector:
    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    return EvocabankCollector(client, max_retries=max_retries, timeout_seconds=timeout_seconds)


async def test_collect_returns_rub_amd_cash_buy_rate() -> None:
    collector = _make_collector(_success_handler)

    points = await assert_returns_valid_rate_points(collector, expected_min_count=1)

    assert len(points) == 1
    point = points[0]
    assert point.source == "Evocabank"
    assert point.base_currency == "RUB"
    assert point.quote_currency == "AMD"
    assert point.value == Decimal("3.8")


async def test_picks_cash_table_not_non_cash_table() -> None:
    collector = _make_collector(_success_handler)

    points = await collector.collect()

    # The Non-Cash table's RUB buy (4.0) must never win over Cash's (3.8).
    assert points[0].value == Decimal("3.8")


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


async def test_missing_rub_row_raises_collector_response_error() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return _html_response(_PAGE_WITHOUT_RUB)

    collector = _make_collector(handler)

    with pytest.raises(CollectorResponseError):
        await collector.collect()
