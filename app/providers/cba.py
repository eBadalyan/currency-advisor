from datetime import UTC, datetime
from decimal import Decimal
from xml.etree import ElementTree

import httpx
from tenacity import retry, stop_after_attempt, wait_exponential

from app.providers.base import Quote, QuoteProvider

CBA_ENDPOINT = "https://api.cba.am/exchangerates.asmx"
SOAP_ACTION = "http://www.cba.am/ExchangeRatesByDateByISO"


class CBAProvider(QuoteProvider):
    """Official daily exchange rates from the Central Bank of Armenia."""

    def __init__(self, timeout: float = 15.0) -> None:
        self._timeout = timeout

    @retry(stop=stop_after_attempt(3), wait=wait_exponential(min=1, max=5), reraise=True)
    async def get_quote(self, symbol: str) -> Quote:
        base, quote = symbol.upper().split("/")
        if quote != "AMD":
            raise ValueError("CBA provider currently supports only */AMD symbols")

        envelope = f"""<?xml version="1.0" encoding="utf-8"?>
        <soap:Envelope xmlns:xsi="http://www.w3.org/2001/XMLSchema-instance"
                       xmlns:xsd="http://www.w3.org/2001/XMLSchema"
                       xmlns:soap="http://schemas.xmlsoap.org/soap/envelope/">
          <soap:Body>
            <ExchangeRatesByDateByISO xmlns="http://www.cba.am/">
              <date>{datetime.now(UTC).date().isoformat()}</date>
              <ISO>{base}</ISO>
            </ExchangeRatesByDateByISO>
          </soap:Body>
        </soap:Envelope>"""

        headers = {"Content-Type": "text/xml; charset=utf-8", "SOAPAction": SOAP_ACTION}
        async with httpx.AsyncClient(timeout=self._timeout) as client:
            response = await client.post(CBA_ENDPOINT, content=envelope, headers=headers)
            response.raise_for_status()

        root = ElementTree.fromstring(response.text)
        rate_node = next((node for node in root.iter() if node.tag.endswith("Rate")), None)
        amount_node = next((node for node in root.iter() if node.tag.endswith("Amount")), None)
        if rate_node is None or not rate_node.text:
            raise RuntimeError("CBA response does not contain a rate")

        amount = (
            Decimal(amount_node.text)
            if amount_node is not None and amount_node.text
            else Decimal(1)
        )
        price = Decimal(rate_node.text) / amount
        return Quote(
            symbol=f"{base}/AMD",
            price=price,
            observed_at=datetime.now(UTC),
            source="Central Bank of Armenia",
            is_official=True,
        )
