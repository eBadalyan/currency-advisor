from __future__ import annotations

from datetime import UTC, datetime
from decimal import Decimal

import httpx
import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from app.collectors.base import RatePoint
from app.collectors.cba import SOURCE_NAME as CBA_SOURCE_NAME
from app.repositories.exchange_rate import ExchangeRateRepository

_NOW = datetime(2026, 7, 21, 12, 0, tzinfo=UTC)


@pytest.fixture(autouse=True)
def _frozen_now(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("app.services.collector_health_service._utcnow", lambda: _NOW)


async def test_collector_health_lists_every_tracked_source(
    db_session: AsyncSession, client: httpx.AsyncClient
) -> None:
    response = await client.get("/health/collectors")

    assert response.status_code == 200
    body = response.json()
    assert len(body) == 8
    assert all(entry["is_stale"] for entry in body)
    assert all(entry["last_observed_at"] is None for entry in body)


async def test_collector_health_reflects_recent_data_as_not_stale(
    db_session: AsyncSession, client: httpx.AsyncClient
) -> None:
    await ExchangeRateRepository(db_session).save(
        RatePoint(CBA_SOURCE_NAME, "RUB", "AMD", Decimal("4.6651"), _NOW)
    )

    response = await client.get("/health/collectors")

    assert response.status_code == 200
    entry = next(
        e for e in response.json() if e["source"] == CBA_SOURCE_NAME and e["base_currency"] == "RUB"
    )
    assert entry["is_stale"] is False
    assert entry["last_observed_at"] is not None
