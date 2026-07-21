from __future__ import annotations

from datetime import UTC, datetime, timedelta
from decimal import Decimal

import httpx
from sqlalchemy.ext.asyncio import AsyncSession

from app.collectors.base import RatePoint
from app.collectors.cba import SOURCE_NAME
from app.repositories.exchange_rate import ExchangeRateRepository

_OBSERVED_AT = datetime(2026, 7, 19, 20, 0, tzinfo=UTC)


def _rate_point(base: str, value: str, observed_at: datetime = _OBSERVED_AT) -> RatePoint:
    return RatePoint(SOURCE_NAME, base, "AMD", Decimal(value), observed_at)


async def test_latest_returns_404_when_no_data(
    db_session: AsyncSession, client: httpx.AsyncClient
) -> None:
    response = await client.get(
        "/rates/latest", params={"base_currency": "RUB", "quote_currency": "AMD"}
    )

    assert response.status_code == 404


async def test_latest_returns_most_recent_rate(
    db_session: AsyncSession, client: httpx.AsyncClient
) -> None:
    await ExchangeRateRepository(db_session).save(_rate_point("RUB", "4.6651"))

    response = await client.get(
        "/rates/latest", params={"base_currency": "rub", "quote_currency": "amd"}
    )

    assert response.status_code == 200
    body = response.json()
    assert body["source"] == SOURCE_NAME
    assert body["base_currency"] == "RUB"
    assert body["quote_currency"] == "AMD"
    assert Decimal(body["value"]) == Decimal("4.6651")


async def test_latest_rejects_invalid_currency_code_length(
    db_session: AsyncSession, client: httpx.AsyncClient
) -> None:
    response = await client.get(
        "/rates/latest", params={"base_currency": "RUBLE", "quote_currency": "AMD"}
    )

    assert response.status_code == 422


async def test_history_returns_points_newest_first(
    db_session: AsyncSession, client: httpx.AsyncClient
) -> None:
    repo = ExchangeRateRepository(db_session)
    older = _rate_point("RUB", "4.5", _OBSERVED_AT - timedelta(days=1))
    newer = _rate_point("RUB", "4.6651", _OBSERVED_AT)
    await repo.bulk_save([older, newer])

    response = await client.get(
        "/rates/history", params={"base_currency": "RUB", "quote_currency": "AMD"}
    )

    assert response.status_code == 200
    body = response.json()
    assert len(body) == 2
    assert Decimal(body[0]["value"]) == Decimal("4.6651")
    assert Decimal(body[1]["value"]) == Decimal("4.5")


async def test_history_returns_empty_list_when_no_data(
    db_session: AsyncSession, client: httpx.AsyncClient
) -> None:
    response = await client.get(
        "/rates/history", params={"base_currency": "RUB", "quote_currency": "AMD"}
    )

    assert response.status_code == 200
    assert response.json() == []


async def test_history_respects_limit_param(
    db_session: AsyncSession, client: httpx.AsyncClient
) -> None:
    repo = ExchangeRateRepository(db_session)
    points = [_rate_point("RUB", "4.5", _OBSERVED_AT - timedelta(days=i)) for i in range(5)]
    await repo.bulk_save(points)

    response = await client.get(
        "/rates/history",
        params={"base_currency": "RUB", "quote_currency": "AMD", "limit": 2},
    )

    assert response.status_code == 200
    assert len(response.json()) == 2
