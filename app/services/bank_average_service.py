from __future__ import annotations

import logging
import statistics
from datetime import UTC, datetime
from decimal import Decimal

from app.collectors.acba_bank import SOURCE_NAME as ACBA_BANK_SOURCE_NAME
from app.collectors.ameriabank import SOURCE_NAME as AMERIABANK_SOURCE_NAME
from app.collectors.base import RatePoint
from app.collectors.evocabank import SOURCE_NAME as EVOCABANK_SOURCE_NAME
from app.collectors.vtb_am import SOURCE_NAME as VTB_AM_SOURCE_NAME
from app.repositories.exchange_rate import ExchangeRateRepository

logger = logging.getLogger(__name__)

# Marked "derived" in the name itself: this source is a computed statistic
# over the four bank sources below, not an external observation — anything
# that enumerates RUB/AMD sources for its own aggregate must exclude it to
# avoid feeding an average back into itself.
SOURCE_NAME = "Bank Average (RUB cash, derived)"
_BASE_CURRENCY = "RUB"
_QUOTE_CURRENCY = "AMD"

_BANK_SOURCE_NAMES = (
    AMERIABANK_SOURCE_NAME,
    EVOCABANK_SOURCE_NAME,
    ACBA_BANK_SOURCE_NAME,
    VTB_AM_SOURCE_NAME,
)

# Below this many reporting banks, the median is too thin a sample to call a
# market rate — skip the write entirely rather than publish a number derived
# from a single, possibly stale or unrepresentative source.
_MIN_BANK_QUORUM = 2


class BankAverageService:
    """Derives a single RUB/AMD "market rate" from the individual bank cash
    rates already collected by AmeriabankCollector/EvocabankCollector/
    AcbaBankCollector/VtbArmeniaCollector.

    Not a RateCollector: it does no external I/O, only reads and writes
    already-persisted data — squarely a Service-layer concern per the
    Collector/Repository boundary.

    Median, not mean: the four banks were deliberately chosen because they
    tend to quote the more competitive RUB rates, so this is not an
    unbiased market sample — a median resists a single outlier (or a
    future fifth/sixth bank) skewing the figure more than a plain average
    would.
    """

    def __init__(self, repository: ExchangeRateRepository) -> None:
        self._repository = repository

    async def compute_and_save(self) -> RatePoint | None:
        values: list[Decimal] = []
        for source in _BANK_SOURCE_NAMES:
            point = await self._repository.get_latest(source, _BASE_CURRENCY, _QUOTE_CURRENCY)
            if point is not None:
                values.append(point.value)

        if len(values) < _MIN_BANK_QUORUM:
            logger.warning(
                "bank_average_service.quorum_not_met",
                extra={"reporting_banks": len(values), "required": _MIN_BANK_QUORUM},
            )
            return None

        median_value = statistics.median(values)
        point = RatePoint(
            source=SOURCE_NAME,
            base_currency=_BASE_CURRENCY,
            quote_currency=_QUOTE_CURRENCY,
            value=median_value,
            observed_at=datetime.now(UTC),
        )
        await self._repository.save(point)
        logger.info(
            "bank_average_service.success",
            extra={"reporting_banks": len(values), "value": str(median_value)},
        )
        return point
