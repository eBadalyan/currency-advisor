from abc import ABC, abstractmethod
from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal


@dataclass(frozen=True, slots=True)
class RatePoint:
    source: str
    base_currency: str
    quote_currency: str
    value: Decimal
    observed_at: datetime


class RateCollector(ABC):
    @abstractmethod
    async def collect(self) -> list[RatePoint]:
        raise NotImplementedError
