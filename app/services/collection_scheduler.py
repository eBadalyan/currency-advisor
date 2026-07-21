from __future__ import annotations

import logging
from collections.abc import Callable
from datetime import UTC, datetime

import httpx
from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.interval import IntervalTrigger
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.collectors.acba_bank import AcbaBankCollector
from app.collectors.ameriabank import AmeriabankCollector
from app.collectors.base import RateCollector, RatePoint
from app.collectors.cba import CBACollector
from app.collectors.cbr import CBRCollector
from app.collectors.evocabank import EvocabankCollector
from app.collectors.vtb_am import VtbArmeniaCollector
from app.core.config import get_settings
from app.repositories.exchange_rate import ExchangeRateRepository
from app.services.rate_service import RateService

logger = logging.getLogger(__name__)

_CBA_JOB_ID = "cba_collection"
_CBR_JOB_ID = "cbr_collection"
_AMERIABANK_JOB_ID = "ameriabank_collection"
_EVOCABANK_JOB_ID = "evocabank_collection"
_ACBA_BANK_JOB_ID = "acba_bank_collection"
_VTB_AM_JOB_ID = "vtb_am_collection"


async def run_collection(service: RateService, collector: RateCollector) -> list[RatePoint] | None:
    """Run one collection, logging and swallowing any failure.

    This is the scheduler's error boundary: a single failed run (source
    down, network blip, malformed response) must not crash the scheduler
    or prevent the next scheduled run — it just gets logged and retried
    on the next tick.
    """
    try:
        points = await service.collect_and_save(collector)
    except Exception:
        logger.exception("collection_scheduler.run_failed")
        return None
    logger.info("collection_scheduler.run_succeeded", extra={"count": len(points)})
    return points


async def _run_scheduled_collection(
    client: httpx.AsyncClient,
    session_factory: async_sessionmaker[AsyncSession],
    collector_factory: Callable[[httpx.AsyncClient], RateCollector],
) -> None:
    async with session_factory() as session:
        service = RateService(ExchangeRateRepository(session))
        collector = collector_factory(client)
        await run_collection(service, collector)


def build_scheduler(
    client: httpx.AsyncClient, session_factory: async_sessionmaker[AsyncSession]
) -> AsyncIOScheduler:
    settings = get_settings()
    scheduler = AsyncIOScheduler()
    jobs: tuple[tuple[str, int, Callable[[httpx.AsyncClient], RateCollector]], ...] = (
        (_CBA_JOB_ID, settings.cba_collection_interval_minutes, CBACollector),
        (_CBR_JOB_ID, settings.cbr_collection_interval_minutes, CBRCollector),
        (_AMERIABANK_JOB_ID, settings.ameriabank_collection_interval_minutes, AmeriabankCollector),
        (_EVOCABANK_JOB_ID, settings.evocabank_collection_interval_minutes, EvocabankCollector),
        (_ACBA_BANK_JOB_ID, settings.acba_bank_collection_interval_minutes, AcbaBankCollector),
        (_VTB_AM_JOB_ID, settings.vtb_am_collection_interval_minutes, VtbArmeniaCollector),
    )
    for job_id, interval_minutes, collector_factory in jobs:
        scheduler.add_job(
            _run_scheduled_collection,
            trigger=IntervalTrigger(minutes=interval_minutes),
            args=(client, session_factory, collector_factory),
            id=job_id,
            next_run_time=datetime.now(UTC),
            max_instances=1,
            coalesce=True,
            misfire_grace_time=60,
        )
    return scheduler
