from __future__ import annotations

from datetime import UTC, datetime
from decimal import Decimal

import httpx
from apscheduler.triggers.interval import IntervalTrigger
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.collectors.base import RateCollector, RatePoint
from app.core.config import get_settings
from app.repositories.exchange_rate import ExchangeRateRepository
from app.services.collection_scheduler import _CBA_JOB_ID, build_scheduler, run_collection
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


def test_build_scheduler_registers_cba_job_with_configured_interval() -> None:
    client = httpx.AsyncClient(transport=httpx.MockTransport(lambda request: httpx.Response(200)))
    session_factory: async_sessionmaker[AsyncSession] = async_sessionmaker(expire_on_commit=False)

    # Not started: this only checks job registration/configuration, not
    # execution, so there is nothing running to shut down afterwards.
    scheduler = build_scheduler(client, session_factory)

    job = scheduler.get_job(_CBA_JOB_ID)
    assert job is not None
    assert isinstance(job.trigger, IntervalTrigger)
    assert (
        job.trigger.interval.total_seconds() == get_settings().cba_collection_interval_minutes * 60
    )
