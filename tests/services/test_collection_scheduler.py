from __future__ import annotations

from datetime import UTC, datetime
from decimal import Decimal

import httpx
import pytest
from apscheduler.triggers.interval import IntervalTrigger
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.collectors.base import RateCollector, RatePoint
from app.core.config import get_settings
from app.repositories.exchange_rate import ExchangeRateRepository
from app.services.collection_scheduler import (
    _ACBA_BANK_JOB_ID,
    _AMERIABANK_JOB_ID,
    _CBA_JOB_ID,
    _CBR_JOB_ID,
    _EVOCABANK_JOB_ID,
    _VTB_AM_JOB_ID,
    build_scheduler,
    run_collection,
)
from app.services.rate_service import RateService

_POINT = RatePoint(
    source="Fake Source",
    base_currency="RUB",
    quote_currency="AMD",
    value=Decimal("4.6"),
    observed_at=datetime(2026, 7, 19, 20, 0, tzinfo=UTC),
)


class _SucceedingCollector(RateCollector):
    async def collect(self) -> list[RatePoint]:
        return [_POINT]


class _FailingCollector(RateCollector):
    async def collect(self) -> list[RatePoint]:
        raise RuntimeError("source is down")


async def test_run_collection_returns_points_on_success(db_session: AsyncSession) -> None:
    service = RateService(ExchangeRateRepository(db_session))

    result = await run_collection(service, _SucceedingCollector())

    assert result == [_POINT]
    assert await ExchangeRateRepository(db_session).exists(
        "Fake Source", "RUB", "AMD", _POINT.observed_at
    )


async def test_run_collection_returns_none_and_does_not_raise_on_failure(
    db_session: AsyncSession,
) -> None:
    service = RateService(ExchangeRateRepository(db_session))

    result = await run_collection(service, _FailingCollector())

    assert result is None


@pytest.mark.parametrize(
    ("job_id", "interval_settings_attr"),
    [
        (_CBA_JOB_ID, "cba_collection_interval_minutes"),
        (_CBR_JOB_ID, "cbr_collection_interval_minutes"),
        (_AMERIABANK_JOB_ID, "ameriabank_collection_interval_minutes"),
        (_EVOCABANK_JOB_ID, "evocabank_collection_interval_minutes"),
        (_ACBA_BANK_JOB_ID, "acba_bank_collection_interval_minutes"),
        (_VTB_AM_JOB_ID, "vtb_am_collection_interval_minutes"),
    ],
)
def test_build_scheduler_registers_job_with_configured_interval(
    job_id: str, interval_settings_attr: str
) -> None:
    client = httpx.AsyncClient(transport=httpx.MockTransport(lambda request: httpx.Response(200)))
    session_factory: async_sessionmaker[AsyncSession] = async_sessionmaker(expire_on_commit=False)

    # Not started: this only checks job registration/configuration, not
    # execution, so there is nothing running to shut down afterwards.
    scheduler = build_scheduler(client, session_factory)

    job = scheduler.get_job(job_id)
    assert job is not None
    assert isinstance(job.trigger, IntervalTrigger)
    expected_minutes = getattr(get_settings(), interval_settings_attr)
    assert job.trigger.interval.total_seconds() == expected_minutes * 60
