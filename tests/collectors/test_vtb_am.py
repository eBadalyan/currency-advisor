from __future__ import annotations

from collections.abc import Callable
from decimal import Decimal

import httpx
import pytest

from app.collectors.exceptions import CollectorResponseError, CollectorTimeoutError
from app.collectors.vtb_am import VtbArmeniaCollector
from tests.collectors.contract import assert_returns_valid_rate_points

# Trimmed down from the real VTB Armenia page (verified live): rates are
# inlined as a plain JS object literal in a <script> tag, not an HTML table.
_PAGE = """
<html><body>
<script>
    let currencyObj =
        {
            'cash': [
                {
                    'USD':
                        {
                            'buy': '362',
                            'sale': '367',
                        }
                    ,
                },
                {
                    'RUB':
                        {
                            'buy': '3.75',
                            'sale': '4.69',
                        }
                    ,
                },
            ],
            'nonCash': [
                {
                    'RUB':
                        {
                            'buy': '4',
                            'sale': '4.82',
                        }
                    ,
                },
            ],
        }
</script>
</body></html>
"""

_PAGE_WITHOUT_CASH_RUB = _PAGE.replace(
    "                {\n"
    "                    'RUB':\n"
    "                        {\n"
    "                            'buy': '3.75',\n"
    "                            'sale': '4.69',\n"
    "                        }\n"
    "                    ,\n"
    "                },\n",
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
) -> VtbArmeniaCollector:
    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    return VtbArmeniaCollector(client, max_retries=max_retries, timeout_seconds=timeout_seconds)


async def test_collect_returns_rub_amd_cash_buy_rate() -> None:
    collector = _make_collector(_success_handler)

    points = await assert_returns_valid_rate_points(collector, expected_min_count=1)

    assert len(points) == 1
    point = points[0]
    assert point.source == "VTB Bank (Armenia)"
    assert point.base_currency == "RUB"
    assert point.quote_currency == "AMD"
    assert point.value == Decimal("3.75")


async def test_picks_cash_section_not_non_cash_section() -> None:
    collector = _make_collector(_success_handler)

    points = await collector.collect()

    # nonCash's RUB buy (4) must never win over cash's (3.75).
    assert points[0].value == Decimal("3.75")


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


async def test_missing_cash_rub_raises_collector_response_error() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return _html_response(_PAGE_WITHOUT_CASH_RUB)

    collector = _make_collector(handler)

    with pytest.raises(CollectorResponseError):
        await collector.collect()
