from __future__ import annotations

import logging
from datetime import UTC, date, datetime
from decimal import Decimal, InvalidOperation
from xml.etree.ElementTree import Element, ParseError, fromstring, tostring
from zoneinfo import ZoneInfo

import httpx
from tenacity import AsyncRetrying, retry_if_exception_type, stop_after_attempt, wait_exponential

from app.collectors.base import RateCollector, RatePoint
from app.collectors.exceptions import CollectorResponseError, CollectorTimeoutError
from app.core.config import get_settings

logger = logging.getLogger(__name__)

_SOAP_ACTION = "http://www.cba.am/ExchangeRatesByDateByISO"
_NAMESPACE = "http://www.cba.am/"
SOURCE_NAME = "Central Bank of Armenia"
_QUOTE_CURRENCY = "AMD"
_COLLECTED_CURRENCIES = ("RUB", "USD")

# CBA publishes one rate per Yerevan calendar day. Using Yerevan's local date
# (rather than naive UTC "today") avoids requesting the wrong day's rate near
# the UTC/Yerevan midnight boundary.
_CBA_TIMEZONE = ZoneInfo("Asia/Yerevan")

_ENVELOPE_TEMPLATE = """<?xml version="1.0" encoding="utf-8"?>
<soap:Envelope xmlns:xsi="http://www.w3.org/2001/XMLSchema-instance"
               xmlns:xsd="http://www.w3.org/2001/XMLSchema"
               xmlns:soap="http://schemas.xmlsoap.org/soap/envelope/">
  <soap:Body>
    <ExchangeRatesByDateByISO xmlns="{namespace}">
      <date>{date}</date>
      <ISO>{iso}</ISO>
    </ExchangeRatesByDateByISO>
  </soap:Body>
</soap:Envelope>"""


def _requested_date() -> date:
    return datetime.now(_CBA_TIMEZONE).date()


def _observed_at(requested: date) -> datetime:
    return datetime(
        requested.year, requested.month, requested.day, tzinfo=_CBA_TIMEZONE
    ).astimezone(UTC)


class CBACollector(RateCollector):
    """Official RUB/AMD and USD/AMD rates from the Central Bank of Armenia SOAP API."""

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
        self._base_url = base_url or settings.cba_base_url
        self._timeout_seconds = (
            timeout_seconds if timeout_seconds is not None else settings.cba_timeout_seconds
        )
        self._max_retries = max_retries if max_retries is not None else settings.cba_max_retries

    async def collect(self) -> list[RatePoint]:
        requested = _requested_date()
        return [await self._fetch_rate(iso, requested) for iso in _COLLECTED_CURRENCIES]

    async def _fetch_rate(self, iso: str, requested: date) -> RatePoint:
        retrying = AsyncRetrying(
            stop=stop_after_attempt(self._max_retries),
            wait=wait_exponential(multiplier=1, min=1, max=5),
            retry=retry_if_exception_type(CollectorTimeoutError),
            reraise=True,
        )
        async for attempt in retrying:
            with attempt:
                return await self._request_rate(iso, requested)
        raise AssertionError("unreachable: AsyncRetrying always raises or returns")

    async def _request_rate(self, iso: str, requested: date) -> RatePoint:
        envelope = _ENVELOPE_TEMPLATE.format(
            namespace=_NAMESPACE, date=requested.isoformat(), iso=iso
        )
        headers = {"Content-Type": "text/xml; charset=utf-8", "SOAPAction": _SOAP_ACTION}

        logger.info("cba_collector.request", extra={"iso": iso, "date": requested.isoformat()})
        try:
            response = await self._client.post(
                self._base_url,
                content=envelope.encode("utf-8"),
                headers=headers,
                timeout=self._timeout_seconds,
            )
        except httpx.TimeoutException as exc:
            logger.warning("cba_collector.timeout", extra={"iso": iso})
            raise CollectorTimeoutError(f"CBA request timed out for {iso}") from exc
        except httpx.HTTPError as exc:
            logger.warning("cba_collector.transport_error", extra={"iso": iso, "error": str(exc)})
            raise CollectorResponseError(f"CBA request failed for {iso}: {exc}") from exc

        if response.status_code >= 400:
            logger.warning(
                "cba_collector.http_error", extra={"iso": iso, "status_code": response.status_code}
            )
            raise CollectorResponseError(f"CBA returned HTTP {response.status_code} for {iso}")

        point = self._parse_rate(iso, requested, response.text)
        logger.info("cba_collector.success", extra={"iso": iso, "value": str(point.value)})
        return point

    def _parse_rate(self, iso: str, requested: date, xml_text: str) -> RatePoint:
        try:
            root = fromstring(xml_text)
        except ParseError as exc:
            raise CollectorResponseError(f"CBA response is not valid XML for {iso}") from exc

        fault = next((node for node in root.iter() if node.tag.endswith("Fault")), None)
        if fault is not None:
            raise CollectorResponseError(
                f"CBA SOAP fault for {iso}: {tostring(fault, encoding='unicode')}"
            )

        rate_nodes = [node for node in root.iter() if node.tag.endswith("ExchangeRate")]
        matching = next((node for node in rate_nodes if self._child_text(node, "ISO") == iso), None)
        if matching is None:
            matching = rate_nodes[0] if rate_nodes else None
        if matching is None:
            raise CollectorResponseError(
                f"CBA response has no exchange rate for {iso} on {requested.isoformat()}"
            )

        rate_text = self._child_text(matching, "Rate")
        if rate_text is None:
            raise CollectorResponseError(f"CBA response is missing Rate for {iso}")
        amount_text = self._child_text(matching, "Amount")

        try:
            rate = Decimal(rate_text)
            amount = Decimal(amount_text) if amount_text else Decimal(1)
            value = rate / amount
        except (InvalidOperation, ZeroDivisionError) as exc:
            raise CollectorResponseError(f"CBA returned an unparsable rate for {iso}") from exc

        return RatePoint(
            source=SOURCE_NAME,
            base_currency=iso,
            quote_currency=_QUOTE_CURRENCY,
            value=value,
            observed_at=_observed_at(requested),
        )

    @staticmethod
    def _child_text(node: Element, tag_suffix: str) -> str | None:
        child = next((c for c in node if c.tag.endswith(tag_suffix)), None)
        return child.text if child is not None else None
