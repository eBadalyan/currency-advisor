from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

from app.collectors.acba_bank import SOURCE_NAME as ACBA_BANK_SOURCE_NAME
from app.collectors.ameriabank import SOURCE_NAME as AMERIABANK_SOURCE_NAME
from app.collectors.base import RatePoint
from app.collectors.cba import SOURCE_NAME as CBA_SOURCE_NAME
from app.collectors.cbr import SOURCE_NAME as CBR_SOURCE_NAME
from app.collectors.evocabank import SOURCE_NAME as EVOCABANK_SOURCE_NAME
from app.collectors.vtb_am import SOURCE_NAME as VTB_AM_SOURCE_NAME
from app.core.config import Settings, get_settings
from app.repositories.exchange_rate import ExchangeRateRepository
from app.services.bank_average_service import SOURCE_NAME as BANK_AVERAGE_SOURCE_NAME

# CBA/CBR publish once per their own calendar day — observed_at is pinned to
# that calendar day, not to collection time, so it can legitimately be many
# hours "old" relative to now on a perfectly healthy day. Staleness for
# these two is "missed calendar days", not scheduler ticks: 2 days catches a
# real break (source down, parser broken) without false-alarming on normal
# same-day timing.
_DAILY_STALE_THRESHOLD = timedelta(days=2)

# The bank sources (and their derived median) write on every scheduler tick,
# so their observed_at is a genuine collection timestamp — staleness here is
# a multiple of the source's own configured interval, wide enough to absorb
# one or two missed/slow ticks without false-alarming on ordinary jitter.
_INTRADAY_STALE_MULTIPLIER = 3


def _utcnow() -> datetime:
    return datetime.now(UTC)


@dataclass(frozen=True, slots=True)
class SourceHealth:
    source: str
    base_currency: str
    quote_currency: str
    last_observed_at: datetime | None
    is_stale: bool


def _tracked_sources(settings: Settings) -> tuple[tuple[str, str, str, timedelta], ...]:
    def _intraday(minutes: int) -> timedelta:
        return timedelta(minutes=minutes * _INTRADAY_STALE_MULTIPLIER)

    return (
        (CBA_SOURCE_NAME, "RUB", "AMD", _DAILY_STALE_THRESHOLD),
        (CBA_SOURCE_NAME, "USD", "AMD", _DAILY_STALE_THRESHOLD),
        (CBR_SOURCE_NAME, "USD", "RUB", _DAILY_STALE_THRESHOLD),
        (
            AMERIABANK_SOURCE_NAME,
            "RUB",
            "AMD",
            _intraday(settings.ameriabank_collection_interval_minutes),
        ),
        (
            EVOCABANK_SOURCE_NAME,
            "RUB",
            "AMD",
            _intraday(settings.evocabank_collection_interval_minutes),
        ),
        (
            ACBA_BANK_SOURCE_NAME,
            "RUB",
            "AMD",
            _intraday(settings.acba_bank_collection_interval_minutes),
        ),
        (VTB_AM_SOURCE_NAME, "RUB", "AMD", _intraday(settings.vtb_am_collection_interval_minutes)),
        (
            BANK_AVERAGE_SOURCE_NAME,
            "RUB",
            "AMD",
            _intraday(settings.bank_average_collection_interval_minutes),
        ),
    )


class CollectorHealthService:
    """Surfaces per-source freshness so a collector silently breaking (a
    bank redesigns its site, a scraper's markup assumption goes stale) is
    visible instead of only showing up as an unexplained gap in history
    weeks later.

    Reads only, no collection or scheduling — a reporting concern over the
    Repository, same boundary as AnalyticsService/BankAverageService.
    """

    def __init__(self, repository: ExchangeRateRepository) -> None:
        self._repository = repository

    async def get_status(self) -> list[SourceHealth]:
        settings = get_settings()
        statuses = []
        for source, base_currency, quote_currency, stale_after in _tracked_sources(settings):
            point = await self._repository.get_latest(source, base_currency, quote_currency)
            statuses.append(
                self._to_health(source, base_currency, quote_currency, point, stale_after)
            )
        return statuses

    @staticmethod
    def _to_health(
        source: str,
        base_currency: str,
        quote_currency: str,
        point: RatePoint | None,
        stale_after: timedelta,
    ) -> SourceHealth:
        if point is None:
            return SourceHealth(source, base_currency, quote_currency, None, is_stale=True)

        is_stale = _utcnow() - point.observed_at > stale_after
        return SourceHealth(source, base_currency, quote_currency, point.observed_at, is_stale)
