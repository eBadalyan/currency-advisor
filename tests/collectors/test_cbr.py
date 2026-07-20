from __future__ import annotations

from collections.abc import Callable
from datetime import UTC, date, datetime
from decimal import Decimal

import httpx
import pytest

from app.collectors.cbr import CBRCollector
from app.collectors.exceptions import CollectorResponseError, CollectorTimeoutError
from tests.collectors.contract import assert_returns_valid_rate_points


def _cp1251_response(xml: str) -> httpx.Response:
    return httpx.Response(200, content=xml.encode("cp1251"))


_SUCCESS_XML = """<?xml version="1.0" encoding="windows-1251"?>
<ValCurs Date="20.07.2026" name="Foreign Currency Market">
<Valute ID="R01235">
<NumCode>840</NumCode>
<CharCode>USD</CharCode>
<Nominal>1</Nominal>
<Name>Доллар США</Name>
<Value>78,3159</Value>
<VunitRate>78,3159</VunitRate>
</Valute>
<Valute ID="R01239">
<NumCode>978</NumCode>
<CharCode>EUR</CharCode>
<Nominal>1</Nominal>
<Name>Евро</Name>
<Value>91,1234</Value>
<VunitRate>91,1234</VunitRate>
</Valute>
</ValCurs>"""

# Real USD entries always have Nominal=1, but the parser must not assume
# that — this uses a synthetic Nominal to exercise the value/nominal math
# through the public collect() path rather than reaching into internals.
_NON_UNIT_NOMINAL_XML = """<?xml version="1.0" encoding="windows-1251"?>
<ValCurs Date="20.07.2026" name="Foreign Currency Market">
<Valute ID="R01235">
<NumCode>840</NumCode>
<CharCode>USD</CharCode>
<Nominal>100</Nominal>
<Name>Доллар США</Name>
<Value>7831,59</Value>
</Valute>
</ValCurs>"""

_MISSING_USD_XML = """<?xml version="1.0" encoding="windows-1251"?>
<ValCurs Date="20.07.2026" name="Foreign Currency Market">
<Valute ID="R01239">
<NumCode>978</NumCode>
<CharCode>EUR</CharCode>
<Nominal>1</Nominal>
<Name>Евро</Name>
<Value>91,1234</Value>
</Valute>
</ValCurs>"""


def _success_handler(request: httpx.Request) -> httpx.Response:
    return _cp1251_response(_SUCCESS_XML)


def _make_collector(
    handler: Callable[[httpx.Request], httpx.Response],
    *,
    max_retries: int = 3,
    timeout_seconds: float = 5.0,
) -> CBRCollector:
    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    return CBRCollector(client, max_retries=max_retries, timeout_seconds=timeout_seconds)


async def test_collect_returns_usd_rub_rate_point() -> None:
    collector = _make_collector(_success_handler)

    points = await assert_returns_valid_rate_points(collector, expected_min_count=1)

    assert len(points) == 1
    point = points[0]
    assert point.base_currency == "USD"
    assert point.quote_currency == "RUB"
    assert point.value == Decimal("78.3159")
    assert point.source == "Central Bank of Russia"


async def test_parses_comma_decimal_and_non_unit_nominal() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return _cp1251_response(_NON_UNIT_NOMINAL_XML)

    collector = _make_collector(handler)

    points = await collector.collect()

    assert points[0].value == Decimal("78.3159")


async def test_retries_on_timeout_then_succeeds() -> None:
    attempts = {"count": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        attempts["count"] += 1
        if attempts["count"] == 1:
            raise httpx.ReadTimeout("mocked timeout", request=request)
        return _cp1251_response(_SUCCESS_XML)

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


async def test_missing_currency_raises_collector_response_error() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return _cp1251_response(_MISSING_USD_XML)

    collector = _make_collector(handler)

    with pytest.raises(CollectorResponseError):
        await collector.collect()


async def test_invalid_xml_raises_collector_response_error() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, content=b"not xml at all")

    collector = _make_collector(handler)

    with pytest.raises(CollectorResponseError):
        await collector.collect()


async def test_observed_at_uses_moscow_calendar_day_converted_to_utc(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr("app.collectors.cbr._requested_date", lambda: date(2026, 7, 20))
    collector = _make_collector(_success_handler)

    points = await collector.collect()

    assert points[0].observed_at == datetime(2026, 7, 19, 21, 0, tzinfo=UTC)
