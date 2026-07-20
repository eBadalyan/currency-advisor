from abc import ABC, abstractmethod
from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal


@dataclass(frozen=True, slots=True)
class Quote:
    symbol: str
    price: Decimal
    observed_at: datetime
    source: str
    is_official: bool = False


class QuoteProvider(ABC):
    @abstractmethod
    async def get_quote(self, symbol: str) -> Quote:
        """Return the latest available quote for a symbol."""
        raise NotImplementedError
