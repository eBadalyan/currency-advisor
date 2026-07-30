from __future__ import annotations

from datetime import UTC, datetime
from decimal import Decimal

import httpx
from sqlalchemy.ext.asyncio import AsyncSession

from app.collectors.ameriabank import SOURCE_NAME as AMERIABANK_SOURCE_NAME
from app.collectors.base import RatePoint
from app.collectors.evocabank import SOURCE_NAME as EVOCABANK_SOURCE_NAME
from app.repositories.exchange_rate import ExchangeRateRepository
from app.services.bank_average_service import SOURCE_NAME as BANK_AVERAGE_SOURCE_NAME

_OBSERVED_AT = datetime(2026, 7, 19, 20, 0, tzinfo=UTC)


async def test_banks_returns_404_when_no_bank_has_data(
    db_session: AsyncSession, client: httpx.AsyncClient
) -> None:
    response = await client.get("/banks")

    assert response.status_code == 404


async def test_banks_returns_partial_data_and_median(
    db_session: AsyncSession, client: httpx.AsyncClient
) -> None:
    repo = ExchangeRateRepository(db_session)
    await repo.save(RatePoint(AMERIABANK_SOURCE_NAME, "RUB", "AMD", Decimal("4.00"), _OBSERVED_AT))
    await repo.save(RatePoint(EVOCABANK_SOURCE_NAME, "RUB", "AMD", Decimal("4.36"), _OBSERVED_AT))
    await repo.save(
        RatePoint(BANK_AVERAGE_SOURCE_NAME, "RUB", "AMD", Decimal("4.18"), _OBSERVED_AT)
    )

    response = await client.get("/banks")

    assert response.status_code == 200
    body = response.json()
    assert len(body["banks"]) == 4
    by_source = {entry["source"]: entry["value"] for entry in body["banks"]}
    assert Decimal(by_source[AMERIABANK_SOURCE_NAME]) == Decimal("4.00")
    assert Decimal(by_source[EVOCABANK_SOURCE_NAME]) == Decimal("4.36")
    assert by_source["ACBA Bank"] is None
    assert by_source["VTB Bank (Armenia)"] is None
    assert Decimal(body["median"]) == Decimal("4.18")
