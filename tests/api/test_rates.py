from __future__ import annotations

from collections.abc import AsyncIterator
from datetime import UTC, datetime, timedelta
from decimal import Decimal

import httpx
import pytest_asyncio
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.collectors.base import RatePoint
from app.collectors.cba import SOURCE_NAME
from app.db.session import get_session
from app.main import app
from app.repositories.exchange_rate import ExchangeRateRepository

_OBSERVED_AT = datetime(2026, 7, 19, 20, 0, tzinfo=UTC)


def _rate_point(base: str, value: str, observed_at: datetime = _OBSERVED_AT) -> RatePoint:
    return RatePoint(SOURCE_NAME, base, "AMD", Decimal(value), observed_at)


@pytest_asyncio.fixture
async def client(
    test_session_factory: async_sessionmaker[AsyncSession],
) -> AsyncIterator[httpx.AsyncClient]:
    # httpx.AsyncClient + ASGITransport runs requests in-process on this
    # test's own event loop — unlike Starlette's TestClient, which drives
    # the app from a separate thread/loop via an anyio portal, and breaks
    # once the DB-backed routes share an async engine with the db_session
    # fixture (connections get opened on one loop, torn down on another).
    #
    # get_session is overridden to the isolated test database too, so the
    # routes under test read/write the same data db_session seeds instead
    # of the real dev database the app's default engine points at.
    async def _get_test_session() -> AsyncIterator[AsyncSession]:
        async with test_session_factory() as session:
            yield session

    app.dependency_overrides[get_session] = _get_test_session
    transport = httpx.ASGITransport(app=app)
    try:
        async with httpx.AsyncClient(
            transport=transport, base_url="http://testserver"
        ) as async_client:
            yield async_client
    finally:
        app.dependency_overrides.pop(get_session, None)


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
