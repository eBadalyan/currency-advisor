from __future__ import annotations

import logging
from datetime import UTC, date, datetime
from decimal import Decimal, InvalidOperation
from xml.etree.ElementTree import Element, ParseError, fromstring
from zoneinfo import ZoneInfo

import httpx
from tenacity import AsyncRetrying, retry_if_exception_type, stop_after_attempt, wait_exponential

from app.collectors.base import RateCollector, RatePoint
from app.collectors.exceptions import CollectorResponseError, CollectorTimeoutError
from app.core.config import get_settings

logger = logging.getLogger(__name__)

SOURCE_NAME = "Central Bank of Russia"
_QUOTE_CURRENCY = "RUB"
_COLLECTED_CURRENCIES = ("USD",)

# CBR publishes one rate per Moscow calendar day. Using Moscow's local date
# (rather than naive UTC "today") avoids requesting the wrong day's rate near
# the UTC/Moscow midnight boundary — same reasoning as CBACollector.
_CBR_TIMEZONE = ZoneInfo("Europe/Moscow")


def _requested_date() -> date:
    return datetime.now(_CBR_TIMEZONE).date()


def _observed_at(requested: date) -> datetime:
    return datetime(
        requested.year, requested.month, requested.day, tzinfo=_CBR_TIMEZONE
    ).astimezone(UTC)


class CBRCollector(RateCollector):
    """Official USD/RUB rate from the Central Bank of Russia's public XML feed.

    Collected as a bridge currency: combined with CBA's official RUB/AMD and
    USD/AMD later (Analytics/Recommendation), it lets a synthetic RUB/AMD be
    computed via USD and compared against the official rate.
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
        self._base_url = base_url or settings.cbr_base_url
        self._timeout_seconds = (
            timeout_seconds if timeout_seconds is not None else settings.cbr_timeout_seconds
        )
        self._max_retries = max_retries if max_retries is not None else settings.cbr_max_retries

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
        params = {"date_req": requested.strftime("%d/%m/%Y")}

        logger.info("cbr_collector.request", extra={"iso": iso, "date": requested.isoformat()})
        try:
            response = await self._client.get(
                self._base_url, params=params, timeout=self._timeout_seconds
            )
        except httpx.TimeoutException as exc:
            logger.warning("cbr_collector.timeout", extra={"iso": iso})
            raise CollectorTimeoutError(f"CBR request timed out for {iso}") from exc
        except httpx.HTTPError as exc:
            logger.warning("cbr_collector.transport_error", extra={"iso": iso, "error": str(exc)})
            raise CollectorResponseError(f"CBR request failed for {iso}: {exc}") from exc

        if response.status_code >= 400:
            logger.warning(
                "cbr_collector.http_error", extra={"iso": iso, "status_code": response.status_code}
            )
            raise CollectorResponseError(f"CBR returned HTTP {response.status_code} for {iso}")

        point = self._parse_rate(iso, requested, response.content)
        logger.info("cbr_collector.success", extra={"iso": iso, "value": str(point.value)})
        return point

    def _parse_rate(self, iso: str, requested: date, xml_bytes: bytes) -> RatePoint:
        try:
            # Parse from raw bytes, not response.text: CBR's feed declares
            # windows-1251 in the XML prolog, not UTF-8, and ElementTree
            # honors that declaration correctly only when given bytes.
            root = fromstring(xml_bytes)
        except ParseError as exc:
            raise CollectorResponseError(f"CBR response is not valid XML for {iso}") from exc

        matching = next(
            (node for node in root.findall("Valute") if self._child_text(node, "CharCode") == iso),
            None,
        )
        if matching is None:
            raise CollectorResponseError(
                f"CBR response has no rate for {iso} on {requested.isoformat()}"
            )

        value_text = self._child_text(matching, "Value")
        nominal_text = self._child_text(matching, "Nominal")
        if value_text is None:
            raise CollectorResponseError(f"CBR response is missing Value for {iso}")

        try:
            # CBR's XML uses a comma as the decimal separator (e.g. "78,3159").
            value = Decimal(value_text.replace(",", "."))
            nominal = Decimal(nominal_text.replace(",", ".")) if nominal_text else Decimal(1)
            rate = value / nominal
        except (InvalidOperation, ZeroDivisionError) as exc:
            raise CollectorResponseError(f"CBR returned an unparsable rate for {iso}") from exc

        return RatePoint(
            source=SOURCE_NAME,
            base_currency=iso,
            quote_currency=_QUOTE_CURRENCY,
            value=rate,
            observed_at=_observed_at(requested),
        )

    @staticmethod
    def _child_text(node: Element, tag: str) -> str | None:
        child = node.find(tag)
        return child.text if child is not None else None
