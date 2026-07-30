from __future__ import annotations

from datetime import UTC, datetime
from decimal import Decimal

import httpx
from sqlalchemy.ext.asyncio import AsyncSession

from app.collectors.base import RatePoint
from app.collectors.cba import SOURCE_NAME as CBA_SOURCE_NAME
from app.repositories.exchange_rate import ExchangeRateRepository

_OBSERVED_AT = datetime(2026, 7, 19, 20, 0, tzinfo=UTC)


async def test_advice_returns_404_when_no_data(
    db_session: AsyncSession, client: httpx.AsyncClient
) -> None:
    response = await client.get("/advice")

    assert response.status_code == 404


async def test_advice_returns_recommendation_when_data_present(
    db_session: AsyncSession, client: httpx.AsyncClient
) -> None:
    await ExchangeRateRepository(db_session).save(
        RatePoint(CBA_SOURCE_NAME, "RUB", "AMD", Decimal("4.6651"), _OBSERVED_AT)
    )

    response = await client.get("/advice")

    assert response.status_code == 200
    body = response.json()
    assert body["action"] in ("exchange_now", "wait", "neutral")
    assert 0 <= Decimal(body["confidence"]) <= 1
    assert isinstance(body["summary"], str) and body["summary"]
    assert isinstance(body["factors"], list) and len(body["factors"]) > 0
    factor = body["factors"][0]
    assert set(factor.keys()) == {"name", "weight", "value", "explanation"}
