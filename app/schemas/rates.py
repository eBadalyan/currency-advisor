from __future__ import annotations

from datetime import datetime
from decimal import Decimal

from pydantic import BaseModel, ConfigDict


class RateResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    source: str
    base_currency: str
    quote_currency: str
    value: Decimal
    observed_at: datetime
