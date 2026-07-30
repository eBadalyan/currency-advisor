from __future__ import annotations

from decimal import Decimal

from pydantic import BaseModel


class BankRateResponse(BaseModel):
    source: str
    value: Decimal | None


class BanksResponse(BaseModel):
    banks: list[BankRateResponse]
    median: Decimal | None
