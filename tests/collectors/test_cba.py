from __future__ import annotations

from collections.abc import Callable
from datetime import UTC, date, datetime
from decimal import Decimal

import httpx
import pytest

from app.collectors.cba import CBACollector
from app.collectors.exceptions import CollectorResponseError, CollectorTimeoutError
from tests.collectors.contract import assert_returns_valid_rate_points

_SUCCESS_BODIES = {
    "RUB": """<?xml version="1.0" encoding="utf-8"?>
<soap:Envelope xmlns:soap="http://schemas.xmlsoap.org/soap/envelope/">
  <soap:Body>
    <ExchangeRatesByDateByISOResponse xmlns="http://www.cba.am/">
      <ExchangeRatesByDateByISOResult>
        <Rates>
          <ExchangeRate>
            <ISO>RUB</ISO>
            <Amount>100</Amount>
            <Rate>460.0</Rate>
          </ExchangeRate>
        </Rates>
      </ExchangeRatesByDateByISOResult>
    </ExchangeRatesByDateByISOResponse>
  </soap:Body>
</soap:Envelope>""",
    "USD": """<?xml version="1.0" encoding="utf-8"?>
<soap:Envelope xmlns:soap="http://schemas.xmlsoap.org/soap/envelope/">
  <soap:Body>
    <ExchangeRatesByDateByISOResponse xmlns="http://www.cba.am/">
      <ExchangeRatesByDateByISOResult>
        <Rates>
          <ExchangeRate>
            <ISO>USD</ISO>
            <Amount>1</Amount>
            <Rate>384.5</Rate>
          </ExchangeRate>
        </Rates>
      </ExchangeRatesByDateByISOResult>
    </ExchangeRatesByDateByISOResponse>
  </soap:Body>
</soap:Envelope>""",
}

_EMPTY_BODY = """<?xml version="1.0" encoding="utf-8"?>
<soap:Envelope xmlns:soap="http://schemas.xmlsoap.org/soap/envelope/">
  <soap:Body>
    <ExchangeRatesByDateByISOResponse xmlns="http://www.cba.am/">
      <ExchangeRatesByDateByISOResult>
        <Rates />
      </ExchangeRatesByDateByISOResult>
    </ExchangeRatesByDateByISOResponse>
  </soap:Body>
</soap:Envelope>"""

_FAULT_BODY = """<?xml version="1.0" encoding="utf-8"?>
<soap:Envelope xmlns:soap="http://schemas.xmlsoap.org/soap/envelope/">
  <soap:Body>
    <soap:Fault>
      <faultcode>soap:Server</faultcode>
      <faultstring>Internal error</faultstring>
    </soap:Fault>
  </soap:Body>
</soap:Envelope>"""


def _iso_from_request(request: httpx.Request) -> str:
    body = request.content.decode("utf-8")
    for iso in ("RUB", "USD"):
        if f"<ISO>{iso}</ISO>" in body:
            return iso
    raise AssertionError(f"could not determine ISO from request body: {body}")


def _success_handler(request: httpx.Request) -> httpx.Response:
    iso = _iso_from_request(request)
    return httpx.Response(200, text=_SUCCESS_BODIES[iso])


def _make_collector(
    handler: Callable[[httpx.Request], httpx.Response],
    *,
    max_retries: int = 3,
    timeout_seconds: float = 5.0,
) -> CBACollector:
    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    return CBACollector(client, max_retries=max_retries, timeout_seconds=timeout_seconds)


async def test_collect_returns_rub_and_usd_rate_points() -> None:
    collector = _make_collector(_success_handler)

    points = await assert_returns_valid_rate_points(collector, expected_min_count=2)

    by_currency = {point.base_currency: point for point in points}
    assert by_currency["RUB"].value == Decimal("4.6")
    assert by_currency["USD"].value == Decimal("384.5")
    assert all(point.quote_currency == "AMD" for point in points)
    assert all(point.source == "Central Bank of Armenia" for point in points)


async def test_retries_on_timeout_then_succeeds() -> None:
    attempts = {"count": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        attempts["count"] += 1
        if attempts["count"] == 1:
            raise httpx.ReadTimeout("mocked timeout", request=request)
        return _success_handler(request)

    collector = _make_collector(handler)

    points = await collector.collect()

    assert attempts["count"] == 3  # RUB: timeout + retry, then USD: success
    assert len(points) == 2


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


async def test_soap_fault_raises_collector_response_error() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, text=_FAULT_BODY)

    collector = _make_collector(handler)

    with pytest.raises(CollectorResponseError):
        await collector.collect()


async def test_missing_rate_raises_collector_response_error() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, text=_EMPTY_BODY)

    collector = _make_collector(handler)

    with pytest.raises(CollectorResponseError):
        await collector.collect()


async def test_observed_at_uses_yerevan_calendar_day_converted_to_utc(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr("app.collectors.cba._requested_date", lambda: date(2026, 7, 20))
    collector = _make_collector(_success_handler)

    points = await collector.collect()

    assert all(point.observed_at == datetime(2026, 7, 19, 20, 0, tzinfo=UTC) for point in points)
